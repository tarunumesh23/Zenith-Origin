from __future__ import annotations

"""
combat/session.py
-----------------
Stateful turn-based combat sessions.

Actions are collected via buttons posted directly in the channel —
one message per player, edited in-place. No DMs required.

Actions
-------
  strike  — full power roll
  guard   — roll at 60% power output, incoming damage reduced by 40%

Round resolution (simultaneous)
---------------------------------
  effective_dmg_to_A = B_power * (0.6 if A guarded else 1.0)
  effective_dmg_to_B = A_power * (0.6 if B guarded else 1.0)
  Round winner = whoever dealt more effective damage to opponent

Match winner = first to win 2 rounds (best-of-3).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import discord

from combat.resolver import Combatant, _roll_power
from cultivation.constants import AFFINITY_DISPLAY, REALM_DISPLAY

log = logging.getLogger("bot.combat.session")

ACTION_TIMEOUT  = 30
GUARD_ABSORB    = 0.40
GUARD_POWER_MOD = 0.60


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class RoundRecord:
    round_num:    int
    a_action:     str
    b_action:     str
    a_power:      float
    b_power:      float
    a_effective:  float   # damage dealt TO b
    b_effective:  float   # damage dealt TO a
    round_winner: str     # "a" | "b" | "tie"
    narrative:    str


@dataclass
class SessionResult:
    winner_id:    int
    loser_id:     int
    rounds:       list[RoundRecord]
    a_wins:       int
    b_wins:       int
    timed_out_id: int | None = None


# ---------------------------------------------------------------------------
# Per-player action view — posted in channel, ephemeral-style via allowed_mentions
# ---------------------------------------------------------------------------

class _ActionView(discord.ui.View):
    """
    Shown in-channel. Only the target player can interact (enforced in
    interaction_check). Other users see a polite rejection.
    """

    def __init__(self, player_id: int) -> None:
        super().__init__(timeout=ACTION_TIMEOUT)
        self.player_id = player_id
        self.chosen: str | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message(
                "These buttons aren't for you.", ephemeral=True
            )
            return False
        return True

    async def _pick(self, interaction: discord.Interaction, action: str, label: str) -> None:
        self.chosen = action
        await interaction.response.edit_message(
            content=f"{label} — locked in. Waiting for opponent…",
            view=None,
        )
        self.stop()

    @discord.ui.button(label="⚔️  Strike", style=discord.ButtonStyle.danger,  custom_id="act_strike")
    async def strike(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._pick(interaction, "strike", "⚔️ **Strike**")

    @discord.ui.button(label="🛡️  Guard",  style=discord.ButtonStyle.primary, custom_id="act_guard")
    async def guard(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._pick(interaction, "guard", "🛡️ **Guard**")

    @discord.ui.button(label="✨  Skill (soon)", style=discord.ButtonStyle.secondary,
                       custom_id="act_skill", disabled=True)
    async def skill(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        pass


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class CombatSession:
    """
    Manages a full best-of-3 fight between two players entirely in-channel.

    Each player gets their own message with action buttons.
    A shared status message is updated after every round.
    """

    def __init__(
        self,
        channel:  discord.abc.Messageable,
        a_row:    dict,
        b_row:    dict,
        a_member: discord.Member,
        b_member: discord.Member,
    ) -> None:
        self.channel  = channel
        self.a_row    = a_row
        self.b_row    = b_row
        self.a_member = a_member
        self.b_member = b_member

        self.a_comb = Combatant(
            discord_id=a_row["discord_id"],
            display_name=a_member.display_name,
            realm=a_row["realm"],
            stage=a_row["stage"],
            affinity=a_row["affinity"] or "earth",
            qi=a_row["qi"],
        )
        self.b_comb = Combatant(
            discord_id=b_row["discord_id"],
            display_name=b_member.display_name,
            realm=b_row["realm"],
            stage=b_row["stage"],
            affinity=b_row["affinity"] or "earth",
            qi=b_row["qi"],
        )

        self.rounds: list[RoundRecord] = []
        self.a_wins = 0
        self.b_wins = 0
        self.a_hp   = 100.0
        self.b_hp   = 100.0

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> SessionResult:
        # Post the persistent status embed once; edit it after each round
        status_msg = await self.channel.send(embed=self._status_embed(round_num=1))

        timed_out_id: int | None = None

        for round_num in range(1, 4):
            if self.a_wins == 2 or self.b_wins == 2:
                break

            # Post action prompts for both players simultaneously
            a_view = _ActionView(self.a_member.id)
            b_view = _ActionView(self.b_member.id)

            a_prompt = await self.channel.send(
                content=f"{self.a_member.mention} — **Round {round_num}**: choose your action!",
                view=a_view,
            )
            b_prompt = await self.channel.send(
                content=f"{self.b_member.mention} — **Round {round_num}**: choose your action!",
                view=b_view,
            )

            # Wait for both players (or timeout)
            await asyncio.gather(a_view.wait(), b_view.wait())

            # Clean up prompt messages
            for msg in (a_prompt, b_prompt):
                try:
                    await msg.delete()
                except Exception:
                    pass

            # Handle timeout forfeit
            timed_out = None
            if a_view.chosen is None:
                timed_out = self.a_member.id
            elif b_view.chosen is None:
                timed_out = self.b_member.id

            if timed_out:
                timed_out_id = timed_out
                winner_id = self.b_member.id if timed_out == self.a_member.id else self.a_member.id
                loser_id  = timed_out
                forfeit_name = self.a_member.display_name if timed_out == self.a_member.id else self.b_member.display_name
                try:
                    await status_msg.edit(embed=discord.Embed(
                        title="⏱️ Timeout — Forfeit",
                        description=f"**{forfeit_name}** failed to act in time and has **forfeited**.",
                        color=discord.Color.dark_gray(),
                    ))
                except Exception:
                    pass
                return SessionResult(
                    winner_id=winner_id, loser_id=loser_id,
                    rounds=self.rounds, a_wins=self.a_wins, b_wins=self.b_wins,
                    timed_out_id=timed_out_id,
                )

            a_action = a_view.chosen or "strike"
            b_action = b_view.chosen or "strike"

            record = self._resolve_round(round_num, a_action, b_action)
            self.rounds.append(record)

            if record.round_winner == "a":
                self.a_wins += 1
            elif record.round_winner == "b":
                self.b_wins += 1

            # Cosmetic HP drain
            self.a_hp = max(0.0, self.a_hp - record.b_effective * 0.8)
            self.b_hp = max(0.0, self.b_hp - record.a_effective * 0.8)

            # Update status embed with round result
            try:
                await status_msg.edit(embed=self._round_result_embed(record, round_num))
            except Exception:
                pass

            # Small pause so players can read the result before the next prompt
            if self.a_wins < 2 and self.b_wins < 2 and round_num < 3:
                await asyncio.sleep(2)

        # Determine winner
        if self.a_wins > self.b_wins:
            winner_id, loser_id = self.a_member.id, self.b_member.id
        elif self.b_wins > self.a_wins:
            winner_id, loser_id = self.b_member.id, self.a_member.id
        else:
            # True tie — challenger (a) loses by convention
            winner_id, loser_id = self.b_member.id, self.a_member.id

        return SessionResult(
            winner_id=winner_id, loser_id=loser_id,
            rounds=self.rounds, a_wins=self.a_wins, b_wins=self.b_wins,
        )

    # ------------------------------------------------------------------
    # Round resolution
    # ------------------------------------------------------------------

    def _resolve_round(self, round_num: int, a_action: str, b_action: str) -> RoundRecord:
        a_raw = _roll_power(self.a_comb, self.b_comb)
        b_raw = _roll_power(self.b_comb, self.a_comb)

        a_power = a_raw * (GUARD_POWER_MOD if a_action == "guard" else 1.0)
        b_power = b_raw * (GUARD_POWER_MOD if b_action == "guard" else 1.0)

        # Damage received — reduced if you guarded
        a_takes = b_power * (1.0 - GUARD_ABSORB if a_action == "guard" else 1.0)
        b_takes = a_power * (1.0 - GUARD_ABSORB if b_action == "guard" else 1.0)

        # Round winner = who dealt more effective damage
        if b_takes > a_takes:
            round_winner = "a"
        elif a_takes > b_takes:
            round_winner = "b"
        else:
            round_winner = "tie"

        narrative = _build_narrative(
            a_name=self.a_member.display_name,
            b_name=self.b_member.display_name,
            a_action=a_action,
            b_action=b_action,
            a_power=a_power,
            b_power=b_power,
            round_winner=round_winner,
        )

        return RoundRecord(
            round_num=round_num,
            a_action=a_action, b_action=b_action,
            a_power=a_power,   b_power=b_power,
            a_effective=b_takes, b_effective=a_takes,
            round_winner=round_winner,
            narrative=narrative,
        )

    # ------------------------------------------------------------------
    # Embeds
    # ------------------------------------------------------------------

    def _hp_bar(self, hp: float, width: int = 10) -> str:
        filled = max(0, int((hp / 100) * width))
        return "█" * filled + "░" * (width - filled)

    def _pip_row(self, wins: int) -> str:
        return " ".join("🟢" if i < wins else "⬜" for i in range(2))

    def _status_embed(self, round_num: int) -> discord.Embed:
        a_realm = REALM_DISPLAY.get(self.a_row["realm"], self.a_row["realm"])
        b_realm = REALM_DISPLAY.get(self.b_row["realm"], self.b_row["realm"])
        a_aff   = AFFINITY_DISPLAY.get(self.a_row.get("affinity") or "", "")
        b_aff   = AFFINITY_DISPLAY.get(self.b_row.get("affinity") or "", "")

        desc = (
            f"**{self.a_member.display_name}** `{a_realm} S{self.a_row['stage']}` {a_aff}\n"
            f"`{self._hp_bar(self.a_hp)}` {self.a_hp:.0f} HP  {self._pip_row(self.a_wins)}\n\n"
            f"**{self.b_member.display_name}** `{b_realm} S{self.b_row['stage']}` {b_aff}\n"
            f"`{self._hp_bar(self.b_hp)}` {self.b_hp:.0f} HP  {self._pip_row(self.b_wins)}\n\n"
            f"*⚔️ Round {round_num} in progress — waiting for both players…*\n"
            f"-# Strike: full power · Guard: absorb 40%, deal 60% · Timeout = forfeit"
        )
        return discord.Embed(
            title=f"⚔️ Round {round_num}",
            description=desc,
            color=discord.Color.blurple(),
        )

    def _round_result_embed(self, record: RoundRecord, round_num: int) -> discord.Embed:
        icon = {"strike": "⚔️", "guard": "🛡️"}

        a_line = (
            f"{icon[record.a_action]} **{self.a_member.display_name}** — "
            f"{record.a_action} · power `{record.a_power:.1f}` · dealt `{record.b_effective:.1f}`"
        )
        b_line = (
            f"{icon[record.b_action]} **{self.b_member.display_name}** — "
            f"{record.b_action} · power `{record.b_power:.1f}` · dealt `{record.a_effective:.1f}`"
        )

        if record.round_winner == "a":
            winner_line = f"🏅 **{self.a_member.display_name}** wins round {round_num}"
            color = discord.Color.green()
        elif record.round_winner == "b":
            winner_line = f"🏅 **{self.b_member.display_name}** wins round {round_num}"
            color = discord.Color.red()
        else:
            winner_line = f"🤝 Round {round_num} — tie"
            color = discord.Color.greyple()

        # Updated HP bars
        a_hp_bar = self._hp_bar(self.a_hp)
        b_hp_bar = self._hp_bar(self.b_hp)

        desc = (
            f"`{a_hp_bar}` {self.a_hp:.0f} HP  {self._pip_row(self.a_wins)}  "
            f"**{self.a_member.display_name}**\n"
            f"`{b_hp_bar}` {self.b_hp:.0f} HP  {self._pip_row(self.b_wins)}  "
            f"**{self.b_member.display_name}**\n\n"
            f"{a_line}\n{b_line}\n\n"
            f"_{record.narrative}_\n\n"
            f"**{winner_line}**"
        )

        next_round = round_num + 1
        if self.a_wins < 2 and self.b_wins < 2 and next_round <= 3:
            desc += f"\n\n*Round {next_round} starting in 2 seconds…*"

        return discord.Embed(
            title=f"Round {round_num} Result",
            description=desc,
            color=color,
        )


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------

def _build_narrative(
    a_name: str, b_name: str,
    a_action: str, b_action: str,
    a_power: float, b_power: float,
    round_winner: str,
) -> str:
    w = a_name if round_winner == "a" else (b_name if round_winner == "b" else None)
    key = (a_action, b_action)

    lines = {
        ("strike", "strike"): (
            f"{a_name} and {b_name} clash head-on, Qi surging between them. "
            + (f"{w} overpowers the exchange." if w else "Neither yields — the shockwave scatters both.")
        ),
        ("strike", "guard"): (
            f"{a_name} drives forward with a fierce strike. {b_name} plants their feet and absorbs the blow. "
            + (f"The raw force shatters the guard — {a_name} takes the round." if round_winner == "a"
               else f"The formation holds — {b_name} endures.")
        ),
        ("guard", "strike"): (
            f"{b_name} presses the attack. {a_name} raises a defensive formation. "
            + (f"The defence crumbles — {b_name} takes the round." if round_winner == "b"
               else f"{a_name}'s guard turns the strike aside cleanly.")
        ),
        ("guard", "guard"): (
            "Both cultivators sink into stillness, conserving Qi behind layered defences. "
            + (f"A fractional gap costs {b_name if round_winner == 'a' else a_name} the round."
               if w else "Neither finds an opening — the round is a stalemate.")
        ),
    }
    return lines.get(key, "The exchange resolves.")