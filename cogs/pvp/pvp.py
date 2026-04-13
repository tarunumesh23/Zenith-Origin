from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from combat.resolver import (
    Combatant, CombatResult, RoundResult, _roll_power,
    qi_steal_amount,
)
from combat.session import CombatSession, SessionResult
from cultivation.constants import (
    REP_AMBUSH_FAIL,
    REP_AMBUSH_SUCCESS,
    REP_WIN_ABOVE_REALM,
    REP_WIN_CHALLENGE,
    REP_WIN_DUEL,
    REALM_ORDER,
)
from db.cultivators import (
    get_cultivator, get_cooldown, set_cooldown, clear_closed_cultivation
)
from db.pvp import (
    accept_challenge, accept_duel,
    add_reputation, apply_crippled,
    apply_foundation_bonus, apply_stage_loss,
    create_challenge, create_duel_request,
    delete_challenge, delete_duel_request,
    get_challenge, get_duel_request,
    get_incoming_challenge, get_incoming_duel_request,
    has_active_ward, is_crippled,
    log_combat, record_fled, record_loss, record_win,
    set_ward, transfer_qi,
)
from training.pvp_bridge import load_modifiers, apply_training_to_round
from ui.embed import build_embed, error_embed, warning_embed
from ui.interaction_utils import safe_defer

log = logging.getLogger("bot.cogs.pvp")

PVP_LOG_CHANNEL = int(os.getenv("PVP_LOG_CHANNEL", "0"))

_REALM_IDX = {r: i for i, r in enumerate(REALM_ORDER)}

CHALLENGE_WINDOW_SECONDS = 3600
DUEL_WINDOW_SECONDS = 300
DUEL_COOLDOWN_DAYS = 7
AMBUSH_COOLDOWN_HOURS = 48
WARD_DURATION_HOURS = 4

SPAR_QI_REWARD = 5


# -------------------------
# Helpers
# -------------------------

def _stage_distance(a: dict, b: dict) -> int:
    return abs((_REALM_IDX[a["realm"]] * 9 + a["stage"]) -
               (_REALM_IDX[b["realm"]] * 9 + b["stage"]))


def _above_realm(winner: dict, loser: dict) -> bool:
    return _REALM_IDX[winner["realm"]] < _REALM_IDX[loser["realm"]]


def _make_combatant(row: dict) -> Combatant:
    return Combatant(
        discord_id=row["discord_id"],
        display_name=row["display_name"],
        realm=row["realm"],
        stage=row["stage"],
        affinity=row["affinity"] or "earth",
        qi=row["qi"],
    )


async def _run_interactive_combat(
    ctx: commands.Context,
    c_row: dict,
    t_row: dict,
    challenger_member: discord.Member,
    target_member: discord.Member,
    dm_mode: bool = False,
) -> SessionResult:

    guild_id = ctx.guild.id if ctx.guild else None

    # Load modifiers
    a_mods = await load_modifiers(c_row["discord_id"], guild_id)
    b_mods = await load_modifiers(t_row["discord_id"], guild_id)

    # FIXED: Proper channel selection
    if dm_mode:
        channel = challenger_member.dm_channel or await challenger_member.create_dm()
    else:
        channel = ctx.channel

    session = CombatSession(
        channel=channel,
        a_row=c_row,
        b_row=t_row,
        a_member=challenger_member,
        b_member=target_member,
        a_mods=a_mods,
        b_mods=b_mods,
    )

    return await session.run()


# -------------------------
# Combat Mode Selector
# -------------------------

class CombatModeView(discord.ui.View):
    def __init__(self, ctx, c_row, t_row, challenger, target, runner):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.c_row = c_row
        self.t_row = t_row
        self.challenger = challenger
        self.target = target
        self.runner = runner

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id in (self.challenger.id, self.target.id)

    @discord.ui.button(label="⚔️ Fight Here", style=discord.ButtonStyle.primary)
    async def fight_here(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(view=None)
        await self.runner(self.ctx, self.c_row, self.t_row,
                          self.challenger, self.target, False)

    @discord.ui.button(label="📩 Fight in DM", style=discord.ButtonStyle.secondary)
    async def fight_dm(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(view=None)

        try:
            await self.challenger.create_dm()
            await self.target.create_dm()
        except Exception:
            await self.ctx.send("❌ Could not open DMs. Using channel.")
            await self.runner(self.ctx, self.c_row, self.t_row,
                              self.challenger, self.target, False)
            return

        await self.runner(self.ctx, self.c_row, self.t_row,
                          self.challenger, self.target, True)


# -------------------------
# Cog
# -------------------------

class PvP(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -------------------------
    # SPAR
    # -------------------------

    @commands.hybrid_command(name="spar")
    async def spar(self, ctx: commands.Context, member: discord.Member):

        if ctx.interaction:
            await safe_defer(ctx.interaction)

        if member.id == ctx.author.id:
            return await ctx.send(embed=error_embed(ctx, "Cannot spar yourself."))

        c_row = await get_cultivator(ctx.author.id)
        t_row = await get_cultivator(member.id)

        if not c_row or not t_row:
            return await ctx.send(embed=error_embed(ctx, "Both must be registered."))

        await ctx.send(
            embed=build_embed(
                ctx,
                title="⚔️ Friendly Spar",
                description=(
                    f"{ctx.author.mention} vs {member.mention}\n\n"
                    "No losses • Winner gets +5 Qi\n"
                    "*30s per round*"
                )
            ),
            view=CombatModeView(ctx, c_row, t_row, ctx.author, member, _run_interactive_combat)
        )

    # -------------------------
    # CHALLENGE ACCEPT
    # -------------------------

    @commands.hybrid_command(name="accept")
    async def accept(self, ctx: commands.Context):

        if ctx.interaction:
            await safe_defer(ctx.interaction)

        pending = await get_incoming_challenge(ctx.author.id)
        if not pending:
            return await ctx.send(embed=error_embed(ctx, "No challenges."))

        challenger_id = pending["challenger_id"]
        challenger = await ctx.guild.fetch_member(challenger_id)

        c_row = await get_cultivator(challenger_id)
        t_row = await get_cultivator(ctx.author.id)

        await delete_challenge(challenger_id, ctx.author.id)

        await ctx.send(
            embed=build_embed(
                ctx,
                title="⚔️ Challenge Accepted",
                description=f"{challenger.mention} vs {ctx.author.mention}"
            ),
            view=CombatModeView(ctx, c_row, t_row, challenger, ctx.author, _run_interactive_combat)
        )

    # -------------------------
    # LOG
    # -------------------------

    async def _pvp_log(self, embed):
        ch = self.bot.get_channel(PVP_LOG_CHANNEL)
        if ch:
            await ch.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PvP(bot))