# combat/session.py  — full replacement
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import discord

from combat.resolver import Combatant, _roll_power
from cultivation.constants import AFFINITY_DISPLAY, REALM_DISPLAY

# Import bridge types — used only when modifiers are supplied
from training.pvp_bridge import TrainingModifiers, TrainingRoundResult, apply_training_to_round

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
    a_effective:  float
    b_effective:  float
    round_winner: str       # "a" | "b" | "tie"
    narrative:    str
    training_notes: list[str] = field(default_factory=list)  # NEW: crit/dodge lines


@dataclass
class SessionResult:
    winner_id:    int
    loser_id:     int
    rounds:       list[RoundRecord]
    a_wins:       int
    b_wins:       int
    timed_out_id: int | None = None


# ---------------------------------------------------------------------------
# Action view (unchanged)
# ---------------------------------------------------------------------------

class _ActionView(discord.ui.View):
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
    def __init__(
        self,
        channel:  discord.abc.Messageable,
        a_row:    dict,
        b_row:    dict,
        a_member: discord.Member,
        b_member: discord.Member,
        # NEW — optional training modifiers loaded by the caller
        a_mods: Optional[TrainingModifiers] = None,
        b_mods: Optional[TrainingModifiers] = None,
    ) -> None:
        self.channel  = channel
        self.a_row    = a_row
        self.b_row    = b_row
        self.a_member = a_member
        self.b_member = b_member
        self.a_mods   = a_mods   # None → legacy mode, no training modifiers
        self.b_mods   = b_mods

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
    # Public entry point (unchanged logic, just passes training notes through)
    # ------------------------------------------------------------------

    async def run(self) -> SessionResult:
        status_msg = await self.channel.send(embed=self._status_embed(round_num=1))
        timed_out_id: int | None = None

        for round_num in range(1, 4):
            if self.a_wins == 2 or self.b_wins == 2:
                break

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

            await asyncio.gather(a_view.wait(), b_view.wait())

            for msg in (a_prompt, b_prompt):
                try:
                    await msg.delete()
                except Exception:
                    pass

            timed_out = None
            if a_view.chosen is None:
                timed_out = self.a_member.id
            elif b_view.chosen is None:
                timed_out = self.b_member.id

            if timed_out:
                timed_out_id = timed_out
                winner_id = self.b_member.id if timed_out == self.a_member.id else self.a_member.id
                loser_id  = timed_out
                forfeit_name = (
                    self.a_member.display_name if timed_out == self.a_member.id
                    else self.b_member.display_name
                )
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

            self.a_hp = max(0.0, self.a_hp - record.b_effective * 0.8)
            self.b_hp = max(0.0, self.b_hp - record.a_effective * 0.8)

            try:
                await status_msg.edit(embed=self._round_result_embed(record, round_num))
            except Exception:
                pass

            if self.a_wins < 2 and self.b_wins < 2 and round_num < 3:
                await asyncio.sleep(2)

        if self.a_wins > self.b_wins:
            winner_id, loser_id = self.a_member.id, self.b_member.id
        elif self.b_wins > self.a_wins:
            winner_id, loser_id = self.b_member.id, self.a_member.id
        else:
            winner_id, loser_id = self.b_member.id, self.a_member.id

        return SessionResult(
            winner_id=winner_id, loser_id=loser_id,
            rounds=self.rounds, a_wins=self.a_wins, b_wins=self.b_wins,
        )

    # ------------------------------------------------------------------
    # Round resolution — branches on whether training mods are present
    # ------------------------------------------------------------------

    def _resolve_round(self, round_num: int, a_action: str, b_action: str) -> RoundRecord:
        a_raw = _roll_power(self.a_comb, self.b_comb)
        b_raw = _roll_power(self.b_comb, self.a_comb)

        training_notes: list[str] = []

        if self.a_mods is not None and self.b_mods is not None:
            # ── Training path ──────────────────────────────────────────
            tr: TrainingRoundResult = apply_training_to_round(
                a_raw_power=a_raw,
                b_raw_power=b_raw,
                a_mods=self.a_mods,
                b_mods=self.b_mods,
                a_action=a_action,
                b_action=b_action,
            )
            a_effective    = tr.a_effective   # damage dealt BY a TO b
            b_effective    = tr.b_effective   # damage dealt BY b TO a
            round_winner   = tr.round_winner
            training_notes = tr.training_notes

            # Reconstruct "displayed power" values from the raw × ATK boost
            a_power = a_raw * self.a_mods.atk_multiplier
            b_power = b_raw * self.b_mods.atk_multiplier

        else:
            # ── Legacy path (no training data) ────────────────────────
            a_power = a_raw * (GUARD_POWER_MOD if a_action == "guard" else 1.0)
            b_power = b_raw * (GUARD_POWER_MOD if b_action == "guard" else 1.0)

            a_takes = b_power * (1.0 - GUARD_ABSORB if a_action == "guard" else 1.0)
            b_takes = a_power * (1.0 - GUARD_ABSORB if b_action == "guard" else 1.0)

            if b_takes > a_takes:
                round_winner = "a"
            elif a_takes > b_takes:
                round_winner = "b"
            else:
                round_winner = "tie"

            a_effective = b_takes
            b_effective = a_takes

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
            a_action=a_action,   b_action=b_action,
            a_power=a_power,     b_power=b_power,
            a_effective=a_effective,
            b_effective=b_effective,
            round_winner=round_winner,
            narrative=narrative,
            training_notes=training_notes,
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

        # Show training power summary if modifiers were loaded
        from training.pvp_bridge import format_training_stats_inline
        a_train = f"\n-# {format_training_stats_inline(self.a_mods)}" if self.a_mods else ""
        b_train = f"\n-# {format_training_stats_inline(self.b_mods)}" if self.b_mods else ""

        desc = (
            f"**{self.a_member.display_name}** `{a_realm} S{self.a_row['stage']}` {a_aff}{a_train}\n"
            f"`{self._hp_bar(self.a_hp)}` {self.a_hp:.0f} HP  {self._pip_row(self.a_wins)}\n\n"
            f"**{self.b_member.display_name}** `{b_realm} S{self.b_row['stage']}` {b_aff}{b_train}\n"
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
            f"{record.a_action} · power `{record.a_power:.1f}` · dealt `{record.a_effective:.1f}`"
        )
        b_line = (
            f"{icon[record.b_action]} **{self.b_member.display_name}** — "
            f"{record.b_action} · power `{record.b_power:.1f}` · dealt `{record.b_effective:.1f}`"
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

        desc = (
            f"`{self._hp_bar(self.a_hp)}` {self.a_hp:.0f} HP  {self._pip_row(self.a_wins)}  "
            f"**{self.a_member.display_name}**\n"
            f"`{self._hp_bar(self.b_hp)}` {self.b_hp:.0f} HP  {self._pip_row(self.b_wins)}  "
            f"**{self.b_member.display_name}**\n\n"
            f"{a_line}\n{b_line}\n\n"
            f"_{record.narrative}_"
        )

        # Append training events (crits, dodges) if any fired this round
        if record.training_notes:
            desc += "\n" + "\n".join(f"-# {n}" for n in record.training_notes)

        desc += f"\n\n**{winner_line}**"

        next_round = round_num + 1
        if self.a_wins < 2 and self.b_wins < 2 and next_round <= 3:
            desc += f"\n\n*Round {next_round} starting in 2 seconds…*"

        return discord.Embed(
            title=f"Round {round_num} Result",
            description=desc,
            color=color,
        )


# ---------------------------------------------------------------------------
# Narrative (unchanged)
# ---------------------------------------------------------------------------

def _build_narrative(
    a_name: str, b_name: str,
    a_action: str, b_action: str,
    a_power: float, b_power: float,
    round_winner: str,
) -> str:
    w   = a_name if round_winner == "a" else (b_name if round_winner == "b" else None)
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