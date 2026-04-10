from __future__ import annotations

"""
combat/session.py
-----------------
Stateful turn-based combat sessions.

Each session lives in memory while a fight is in progress.
Both players pick an action via Discord buttons; once both have chosen
(or the timeout elapses) the round is resolved and the next begins.

Actions
-------
  strike  — full power roll; normal damage
  guard   — roll at 60% power output, but incoming damage reduced by 40%
  skill   — reserved for future abilities; currently disabled in the view

Round resolution (simultaneous)
--------------------------------
  effective_dmg_to_A = B_power * (0.6 if A chose guard else 1.0)
  effective_dmg_to_B = A_power * (0.6 if B chose guard else 1.0)
  Round winner = whoever dealt more effective damage

Match winner = first to win 2 rounds (best-of-3).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Coroutine

import discord

from combat.resolver import Combatant

# _roll_power is a module-private helper in resolver.py.
# If you rename it there, update this import.
# Cleaner long-term: move it to combat.utils and import publicly.
try:
    from combat.resolver import _roll_power
except ImportError as e:
    raise ImportError(
        "session.py needs _roll_power from combat.resolver. "
        "Export it publicly or move it to combat.utils."
    ) from e
from cultivation.constants import AFFINITY_DISPLAY, REALM_DISPLAY

log = logging.getLogger("bot.combat.session")

ACTION_TIMEOUT  = 30          # seconds each player has to pick
GUARD_ABSORB    = 0.40        # defender takes 40% less
GUARD_POWER_MOD = 0.60        # guarding player deals 60% power


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class RoundRecord:
    round_num:       int
    a_action:        str           # "strike" | "guard"
    b_action:        str
    a_power:         float
    b_power:         float
    a_effective:     float         # damage dealt TO b
    b_effective:     float         # damage dealt TO a
    round_winner:    str           # "a" | "b" | "tie"
    narrative:       str


@dataclass
class SessionResult:
    winner_id:    int
    loser_id:     int
    rounds:       list[RoundRecord]
    a_wins:       int
    b_wins:       int
    timed_out_id: int | None = None   # player who timed out (forfeit)


# ---------------------------------------------------------------------------
# Combat view — buttons shown to a single player
# ---------------------------------------------------------------------------

class _ActionView(discord.ui.View):
    """Shown to one player. Resolves when they pick or time out."""

    def __init__(self, player_id: int, timeout: float = ACTION_TIMEOUT) -> None:
        super().__init__(timeout=timeout)
        self.player_id = player_id
        self.chosen:    str | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message(
                "This is not your fight.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Strike", style=discord.ButtonStyle.danger, custom_id="action_strike")
    async def strike(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.chosen = "strike"
        await interaction.response.edit_message(
            content="⚔️ **Strike** locked in — waiting for opponent…", view=None
        )
        self.stop()

    @discord.ui.button(label="Guard", style=discord.ButtonStyle.primary, custom_id="action_guard")
    async def guard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.chosen = "guard"
        await interaction.response.edit_message(
            content="🛡️ **Guard** locked in — waiting for opponent…", view=None
        )
        self.stop()

    @discord.ui.button(label="Skill (soon)", style=discord.ButtonStyle.secondary,
                       custom_id="action_skill", disabled=True)
    async def skill(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass  # disabled — never fires


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class CombatSession:
    """
    Manages a full best-of-3 fight between two players.

    Usage
    -----
        session = CombatSession(channel, a_row, b_row, a_member, b_member)
        result  = await session.run()
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

        self.rounds:  list[RoundRecord] = []
        self.a_wins   = 0
        self.b_wins   = 0

        # HP — cosmetic only, starts at 100
        self.a_hp = 100.0
        self.b_hp = 100.0

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> SessionResult:
        """Run the full session and return the result."""
        timed_out_id: int | None = None

        for round_num in range(1, 4):
            if self.a_wins == 2 or self.b_wins == 2:
                break

            a_action, b_action, timed_out = await self._collect_actions(round_num)

            # A player timed out → they forfeit
            if timed_out:
                timed_out_id = timed_out
                winner_id = self.b_member.id if timed_out == self.a_member.id else self.a_member.id
                loser_id  = timed_out
                await self.channel.send(
                    embed=self._timeout_embed(timed_out)
                )
                return SessionResult(
                    winner_id=winner_id,
                    loser_id=loser_id,
                    rounds=self.rounds,
                    a_wins=self.a_wins,
                    b_wins=self.b_wins,
                    timed_out_id=timed_out_id,
                )

            record = self._resolve_round(round_num, a_action, b_action)
            self.rounds.append(record)

            if record.round_winner == "a":
                self.a_wins += 1
            elif record.round_winner == "b":
                self.b_wins += 1
            # tie → no win awarded

            # Cosmetic HP
            self.a_hp = max(0.0, self.a_hp - record.b_effective * 0.8)
            self.b_hp = max(0.0, self.b_hp - record.a_effective * 0.8)

            await self.channel.send(embed=self._round_embed(record, round_num))

        # Determine overall winner
        if self.a_wins > self.b_wins:
            winner_id, loser_id = self.a_member.id, self.b_member.id
        elif self.b_wins > self.a_wins:
            winner_id, loser_id = self.b_member.id, self.a_member.id
        else:
            # True tie — challenger (a) loses by convention
            winner_id, loser_id = self.b_member.id, self.a_member.id

        return SessionResult(
            winner_id=winner_id,
            loser_id=loser_id,
            rounds=self.rounds,
            a_wins=self.a_wins,
            b_wins=self.b_wins,
        )

    # ------------------------------------------------------------------
    # Action collection
    # ------------------------------------------------------------------

    async def _collect_actions(
        self, round_num: int
    ) -> tuple[str, str, int | None]:
        """
        DM both players their action buttons simultaneously.
        Returns (a_action, b_action, timed_out_player_id_or_None).
        """
        a_view = _ActionView(self.a_member.id)
        b_view = _ActionView(self.b_member.id)

        status_msg = await self.channel.send(
            embed=self._waiting_embed(round_num)
        )

        # Send DMs
        a_msg = b_msg = None
        try:
            a_msg = await self.a_member.send(
                content=f"⚔️ **Round {round_num}** — choose your action:",
                view=a_view,
            )
        except discord.Forbidden:
            pass

        try:
            b_msg = await self.b_member.send(
                content=f"⚔️ **Round {round_num}** — choose your action:",
                view=b_view,
            )
        except discord.Forbidden:
            pass

        # Wait for both (or timeout)
        await asyncio.gather(
            a_view.wait(),
            b_view.wait(),
        )

        # Clean up DMs
        for msg in (a_msg, b_msg):
            if msg:
                try:
                    await msg.delete()
                except Exception:
                    pass

        # Check for timeouts → forfeit
        if a_view.chosen is None:
            return "strike", "strike", self.a_member.id
        if b_view.chosen is None:
            return "strike", "strike", self.b_member.id

        return a_view.chosen, b_view.chosen, None

    # ------------------------------------------------------------------
    # Round resolution
    # ------------------------------------------------------------------

    def _resolve_round(
        self, round_num: int, a_action: str, b_action: str
    ) -> RoundRecord:
        # Base power rolls
        a_raw = _roll_power(self.a_comb, self.b_comb)
        b_raw = _roll_power(self.b_comb, self.a_comb)

        # Guarding reduces your output
        a_power = a_raw * (GUARD_POWER_MOD if a_action == "guard" else 1.0)
        b_power = b_raw * (GUARD_POWER_MOD if b_action == "guard" else 1.0)

        # Damage received is reduced if you guarded
        a_effective = b_power * (1.0 - GUARD_ABSORB if a_action == "guard" else 1.0)
        b_effective = a_power * (1.0 - GUARD_ABSORB if b_action == "guard" else 1.0)

        # Round winner = whoever dealt more effective damage
        if b_effective > a_effective:
            round_winner = "a"   # a dealt more TO b
        elif a_effective > b_effective:
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
            a_action=a_action,
            b_action=b_action,
            a_power=a_power,
            b_power=b_power,
            a_effective=a_effective,
            b_effective=b_effective,
            round_winner=round_winner,
            narrative=narrative,
        )

    # ------------------------------------------------------------------
    # Embeds
    # ------------------------------------------------------------------

    def _waiting_embed(self, round_num: int) -> discord.Embed:
        a_realm = REALM_DISPLAY.get(self.a_row["realm"], self.a_row["realm"])
        b_realm = REALM_DISPLAY.get(self.b_row["realm"], self.b_row["realm"])
        a_aff   = AFFINITY_DISPLAY.get(self.a_row["affinity"] or "", "")
        b_aff   = AFFINITY_DISPLAY.get(self.b_row["affinity"] or "", "")

        # HP bars (10 chars)
        a_filled = int((self.a_hp / 100) * 10)
        b_filled = int((self.b_hp / 100) * 10)
        a_bar = "█" * a_filled + "░" * (10 - a_filled)
        b_bar = "█" * b_filled + "░" * (10 - b_filled)

        pips_a = " ".join("🟢" if i < self.a_wins else "⬜" for i in range(2))
        pips_b = " ".join("🟢" if i < self.b_wins else "⬜" for i in range(2))

        desc = (
            f"**{self.a_member.display_name}** `{a_realm} S{self.a_row['stage']}` {a_aff}\n"
            f"`{a_bar}` {self.a_hp:.0f} HP  {pips_a}\n\n"
            f"**{self.b_member.display_name}** `{b_realm} S{self.b_row['stage']}` {b_aff}\n"
            f"`{b_bar}` {self.b_hp:.0f} HP  {pips_b}\n\n"
            f"*Both cultivators have been sent their action choices via DM.*\n"
            f"*Waiting… ({ACTION_TIMEOUT}s timeout)*"
        )

        embed = discord.Embed(
            title=f"⚔️ Round {round_num} — Choose Your Action",
            description=desc,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Strike for raw power · Guard to absorb damage · Skill coming soon")
        return embed

    def _round_embed(self, record: RoundRecord, round_num: int) -> discord.Embed:
        action_icon = {"strike": "⚔️", "guard": "🛡️"}

        a_line = f"{action_icon[record.a_action]} **{self.a_member.display_name}** {record.a_action}s — power `{record.a_power:.1f}`"
        b_line = f"{action_icon[record.b_action]} **{self.b_member.display_name}** {record.b_action}s — power `{record.b_power:.1f}`"

        if record.round_winner == "a":
            winner_line = f"\n\n🏅 **{self.a_member.display_name}** wins round {round_num}"
            color = discord.Color.green()
        elif record.round_winner == "b":
            winner_line = f"\n\n🏅 **{self.b_member.display_name}** wins round {round_num}"
            color = discord.Color.red()
        else:
            winner_line = f"\n\n🤝 Round {round_num} — tie"
            color = discord.Color.greyple()

        pips_a = " ".join("🟢" if i < self.a_wins else "⬜" for i in range(2))
        pips_b = " ".join("🟢" if i < self.b_wins else "⬜" for i in range(2))

        desc = (
            f"{a_line}\n{b_line}\n\n"
            f"_{record.narrative}_"
            + winner_line
            + f"\n\n{pips_a} **{self.a_member.display_name}** vs **{self.b_member.display_name}** {pips_b}"
        )

        return discord.Embed(
            title=f"Round {round_num} Result",
            description=desc,
            color=color,
        )

    def _timeout_embed(self, timed_out_id: int) -> discord.Embed:
        who = self.a_member if timed_out_id == self.a_member.id else self.b_member
        return discord.Embed(
            title="⏱️ Timeout — Forfeit",
            description=(
                f"**{who.display_name}** failed to choose an action in time.\n\n"
                f"They have **forfeited** the match."
            ),
            color=discord.Color.dark_gray(),
        )


# ---------------------------------------------------------------------------
# Narrative builder
# ---------------------------------------------------------------------------

def _build_narrative(
    a_name: str,
    b_name: str,
    a_action: str,
    b_action: str,
    a_power: float,
    b_power: float,
    round_winner: str,
) -> str:
    key = (a_action, b_action)
    w   = a_name if round_winner == "a" else (b_name if round_winner == "b" else None)

    lines = {
        ("strike", "strike"): (
            f"{a_name} and {b_name} clash head-on, Qi surging between them. "
            + (f"{w} overpowers the exchange." if w else "Neither yields — the impact scatters both.")
        ),
        ("strike", "guard"): (
            f"{a_name} drives forward with a fierce strike. "
            f"{b_name} plants their feet and absorbs the blow. "
            + (f"The raw force breaks through — {a_name} wins the exchange." if round_winner == "a"
               else f"The guard holds — {b_name} endures.")
        ),
        ("guard", "strike"): (
            f"{b_name} presses the attack. {a_name} raises a defensive formation. "
            + (f"The defence crumbles under the pressure — {b_name} takes the round." if round_winner == "b"
               else f"{a_name}'s guard turns the strike aside.")
        ),
        ("guard", "guard"): (
            f"Both cultivators sink into stillness, conserving Qi behind layered defences. "
            + (f"A fractional opening costs {b_name if round_winner == 'a' else a_name} the round."
               if w else "Neither finds a gap — the round is a stalemate.")
        ),
    }

    return lines.get(key, "The exchange resolves.")