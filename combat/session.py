# combat/session.py

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import discord

from combat.resolver import Combatant, _roll_power
from cultivation.constants import AFFINITY_DISPLAY, REALM_DISPLAY
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
    round_num: int
    a_action: str
    b_action: str
    a_power: float
    b_power: float
    a_effective: float
    b_effective: float
    round_winner: str
    narrative: str
    training_notes: list[str] = field(default_factory=list)


@dataclass
class SessionResult:
    winner_id: int
    loser_id: int
    rounds: list[RoundRecord]
    a_wins: int
    b_wins: int
    timed_out_id: int | None = None


# ---------------------------------------------------------------------------
# Action View
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

    async def _pick(self, interaction: discord.Interaction, action: str):
        self.chosen = action
        await interaction.response.edit_message(
            content=f"**{action.title()} locked in.** Waiting...",
            view=None,
        )
        self.stop()

    @discord.ui.button(label="⚔️ Strike", style=discord.ButtonStyle.danger)
    async def strike(self, interaction: discord.Interaction, _):
        await self._pick(interaction, "strike")

    @discord.ui.button(label="🛡️ Guard", style=discord.ButtonStyle.primary)
    async def guard(self, interaction: discord.Interaction, _):
        await self._pick(interaction, "guard")


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class CombatSession:
    def __init__(
        self,
        channel: discord.abc.Messageable,
        a_row: dict,
        b_row: dict,
        a_member: discord.Member,
        b_member: discord.Member,
        a_mods: Optional[TrainingModifiers] = None,
        b_mods: Optional[TrainingModifiers] = None,
        dm_mode: bool = False,  # ✅ NEW
    ) -> None:
        self.channel = channel
        self.a_row = a_row
        self.b_row = b_row
        self.a_member = a_member
        self.b_member = b_member
        self.a_mods = a_mods
        self.b_mods = b_mods
        self.dm_mode = dm_mode  # ✅

        self.rounds: list[RoundRecord] = []
        self.a_wins = 0
        self.b_wins = 0
        self.a_hp = 100.0
        self.b_hp = 100.0

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

    # ------------------------------------------------------------------
    # Channel / DM Routing
    # ------------------------------------------------------------------

    async def _get_channels(self):
        if not self.dm_mode:
            return self.channel, self.channel

        try:
            a_dm = await self.a_member.create_dm()
            b_dm = await self.b_member.create_dm()
            return a_dm, b_dm
        except Exception:
            log.warning("DM failed → fallback to channel")
            return self.channel, self.channel

    # ------------------------------------------------------------------
    # Main Run
    # ------------------------------------------------------------------

    async def run(self) -> SessionResult:
        a_chan, b_chan = await self._get_channels()

        status_msg = await self.channel.send(embed=self._status_embed(1))

        timed_out_id = None

        for round_num in range(1, 4):

            if self.a_wins == 2 or self.b_wins == 2:
                break

            a_view = _ActionView(self.a_member.id)
            b_view = _ActionView(self.b_member.id)

            a_prompt = await a_chan.send(
                f"**Round {round_num}** — choose your action:",
                view=a_view
            )
            b_prompt = await b_chan.send(
                f"**Round {round_num}** — choose your action:",
                view=b_view
            )

            await asyncio.gather(a_view.wait(), b_view.wait())

            # Cleanup
            for msg in (a_prompt, b_prompt):
                try:
                    await msg.delete()
                except:
                    pass

            if a_view.chosen is None:
                timed_out_id = self.a_member.id
            elif b_view.chosen is None:
                timed_out_id = self.b_member.id

            if timed_out_id:
                winner = self.b_member if timed_out_id == self.a_member.id else self.a_member
                loser  = self.a_member if timed_out_id == self.a_member.id else self.b_member

                await status_msg.edit(
                    embed=discord.Embed(
                        title="⏱️ Timeout",
                        description=f"**{loser.display_name}** failed to act.\n\n**{winner.display_name} wins!**",
                        color=discord.Color.dark_gray()
                    )
                )

                return SessionResult(
                    winner_id=winner.id,
                    loser_id=loser.id,
                    rounds=self.rounds,
                    a_wins=self.a_wins,
                    b_wins=self.b_wins,
                    timed_out_id=timed_out_id,
                )

            record = self._resolve_round(round_num, a_view.chosen, b_view.chosen)
            self.rounds.append(record)

            if record.round_winner == "a":
                self.a_wins += 1
            elif record.round_winner == "b":
                self.b_wins += 1

            self.a_hp = max(0, self.a_hp - record.b_effective * 0.8)
            self.b_hp = max(0, self.b_hp - record.a_effective * 0.8)

            await status_msg.edit(embed=self._round_embed(record, round_num))

            await asyncio.sleep(2)

        winner = self.a_member if self.a_wins > self.b_wins else self.b_member
        loser  = self.b_member if winner == self.a_member else self.a_member

        return SessionResult(
            winner_id=winner.id,
            loser_id=loser.id,
            rounds=self.rounds,
            a_wins=self.a_wins,
            b_wins=self.b_wins,
        )

    # ------------------------------------------------------------------
    # Round Logic (unchanged core)
    # ------------------------------------------------------------------

    def _resolve_round(self, round_num, a_action, b_action):

        a_raw = _roll_power(self.a_comb, self.b_comb)
        b_raw = _roll_power(self.b_comb, self.a_comb)

        training_notes = []

        if self.a_mods and self.b_mods:
            tr: TrainingRoundResult = apply_training_to_round(
                a_raw, b_raw,
                self.a_mods, self.b_mods,
                a_action, b_action
            )

            return RoundRecord(
                round_num,
                a_action, b_action,
                a_raw, b_raw,
                tr.a_effective,
                tr.b_effective,
                tr.round_winner,
                "A clash of enhanced power!",
                tr.training_notes
            )

        # fallback
        a_power = a_raw * (GUARD_POWER_MOD if a_action == "guard" else 1)
        b_power = b_raw * (GUARD_POWER_MOD if b_action == "guard" else 1)

        if a_power > b_power:
            winner = "a"
        elif b_power > a_power:
            winner = "b"
        else:
            winner = "tie"

        return RoundRecord(
            round_num,
            a_action, b_action,
            a_power, b_power,
            a_power, b_power,
            winner,
            "A direct clash of Qi.",
            []
        )

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _status_embed(self, round_num):
        return discord.Embed(
            title=f"⚔️ Combat Begins",
            description=f"Round {round_num} starting...",
            color=discord.Color.blurple()
        )

    def _round_embed(self, record, round_num):
        return discord.Embed(
            title=f"Round {round_num}",
            description=(
                f"{self.a_member.display_name} `{record.a_power:.1f}` vs "
                f"{self.b_member.display_name} `{record.b_power:.1f}`\n\n"
                f"Winner: **{record.round_winner.upper()}**"
            ),
            color=discord.Color.green() if record.round_winner == "a" else discord.Color.red()
        )