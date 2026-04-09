from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from combat.resolver import Combatant, CombatResult, resolve_combat, qi_steal_amount
from cultivation.constants import (
    AFFINITY_DISPLAY,
    REALM_DISPLAY,
    REP_AMBUSH_FAIL,
    REP_AMBUSH_SUCCESS,
    REP_FLEE,
    REP_WIN_ABOVE_REALM,
    REP_WIN_CHALLENGE,
    REP_WIN_DUEL,
    REALM_ORDER,
)
from db.cultivators import get_cultivator, get_cooldown, set_cooldown, clear_closed_cultivation
from db.pvp import (
    accept_challenge,
    accept_duel,
    add_reputation,
    apply_crippled,
    apply_foundation_bonus,
    apply_stage_loss,
    clear_ward,
    create_challenge,
    create_duel_request,
    delete_challenge,
    delete_duel_request,
    get_challenge,
    get_duel_request,
    get_incoming_challenge,
    has_active_ward,
    is_crippled,
    log_combat,
    record_fled,
    record_loss,
    record_win,
    set_ward,
    transfer_qi,
)
from ui.embed import build_embed, error_embed, warning_embed

log = logging.getLogger("bot.cogs.pvp")

PVP_LOG_CHANNEL = int(os.getenv("PVP_LOG_CHANNEL", "0"))

# Realm index for stage-gap checks
_REALM_IDX = {r: i for i, r in enumerate(REALM_ORDER)}

CHALLENGE_WINDOW_SECONDS = 3600   # 1 hour to accept a Dao Challenge
DUEL_WINDOW_SECONDS      = 300    # 5 minutes to accept a Life-and-Death Duel request
DUEL_COOLDOWN_DAYS       = 7
AMBUSH_COOLDOWN_HOURS    = 48
WARD_DURATION_HOURS      = 4      # ward active for 4 hours after /ward


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stage_distance(a: dict, b: dict) -> int:
    """Absolute stage distance between two cultivators across realms."""
    a_abs = _REALM_IDX[a["realm"]] * 9 + a["stage"]
    b_abs = _REALM_IDX[b["realm"]] * 9 + b["stage"]
    return abs(a_abs - b_abs)


def _above_realm(winner: dict, loser: dict) -> bool:
    """True if the winner is in a lower realm than loser (upset win)."""
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


def _round_bar(rounds: list) -> str:
    """Visual round summary e.g. ✅ ❌ ✅"""
    icons = []
    for r in rounds:
        icons.append("🟢" if r.challenger_won else "🔴")
    return "  ".join(icons)


def _combat_embed(
    ctx: commands.Context,
    title: str,
    challenger: discord.Member,
    target: discord.Member,
    result: CombatResult,
    color: discord.Color,
    extra_lines: str = "",
) -> discord.Embed:
    winner = challenger if result.challenger_won else target
    loser  = target     if result.challenger_won else challenger

    desc = (
        f"**{challenger.display_name}** vs **{target.display_name}**\n\n"
        f"{'🟢 Challenger':<20} {'🔴 Target'}\n"
        + "\n".join(
            f"`{r.challenger_power:6.1f}` {'✅' if r.challenger_won else '❌'}  "
            f"`{r.target_power:6.1f}`"
            for r in result.rounds
        )
        + f"\n\n**Rounds:** {_round_bar(result.rounds)}"
        + f"\n\n⚔️ **{winner.display_name}** wins {result.challenger_wins if result.challenger_won else result.target_wins}–"
        + f"{result.target_wins if result.challenger_won else result.challenger_wins}"
    )

    if extra_lines:
        desc += f"\n\n{extra_lines}"

    return build_embed(ctx, title=title, description=desc, color=color, show_footer=True)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class PvP(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -----------------------------------------------------------------------
    # /spar @user
    # -----------------------------------------------------------------------

    @commands.hybrid_command(name="spar", description="Friendly spar — no stakes, no cooldown")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def spar(self, ctx: commands.Context, member: discord.Member) -> None:
        if member.id == ctx.author.id:
            await ctx.send(embed=error_embed(ctx, "You cannot spar yourself."), ephemeral=True)
            return

        c_row = await get_cultivator(ctx.author.id)
        t_row = await get_cultivator(member.id)

        if not c_row or not t_row:
            await ctx.send(embed=error_embed(ctx, "Both cultivators must be registered."), ephemeral=True)
            return

        challenger = _make_combatant(c_row)
        target     = _make_combatant(t_row)
        result     = resolve_combat(challenger, target)

        winner_row = c_row if result.challenger_won else t_row
        winner_mem = ctx.author if result.challenger_won else member

        # Small Qi boost for winner — no DB write needed for loser
        qi_gain = 5
        from db.cultivators import add_qi
        await add_qi(winner_row["discord_id"], qi_gain)

        embed = _combat_embed(
            ctx,
            title="⚔️ Sparring Match",
            challenger=ctx.author,
            target=member,
            result=result,
            color=discord.Color.blue(),
            extra_lines=f"🎁 **{winner_mem.display_name}** gains `+{qi_gain}` Qi",
        )
        await ctx.send(embed=embed)

    # -----------------------------------------------------------------------
    # /challenge @user  (issue)
    # -----------------------------------------------------------------------

    @commands.hybrid_command(name="challenge", description="Issue a formal Dao Challenge")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def challenge(self, ctx: commands.Context, member: discord.Member) -> None:
        if member.id == ctx.author.id:
            await ctx.send(embed=error_embed(ctx, "You cannot challenge yourself."), ephemeral=True)
            return

        c_row = await get_cultivator(ctx.author.id)
        t_row = await get_cultivator(member.id)

        if not c_row or not t_row:
            await ctx.send(embed=error_embed(ctx, "Both cultivators must be registered."), ephemeral=True)
            return

        # Stage restriction — within 2 stages
        if _stage_distance(c_row, t_row) > 2:
            await ctx.send(
                embed=error_embed(
                    ctx,
                    f"You may only challenge cultivators within **2 stages** of your own level.\n"
                    f"You are **{c_row['realm'].replace('_',' ').title()} Stage {c_row['stage']}**, "
                    f"they are **{t_row['realm'].replace('_',' ').title()} Stage {t_row['stage']}**.",
                ),
                ephemeral=True,
            )
            return

        # Check for existing pending challenge
        existing = await get_challenge(ctx.author.id, member.id)
        if existing:
            await ctx.send(
                embed=warning_embed(ctx, "You already have a pending challenge against this cultivator."),
                ephemeral=True,
            )
            return

        expires = datetime.now(timezone.utc) + timedelta(seconds=CHALLENGE_WINDOW_SECONDS)
        await create_challenge(ctx.author.id, member.id, expires)

        embed = build_embed(
            ctx,
            title="⚡ Dao Challenge Issued",
            description=(
                f"{ctx.author.mention} has issued a **Dao Challenge** to {member.mention}!\n\n"
                f"You have **1 hour** to `/accept` or `/flee`.\n"
                f"Fleeing costs **15 reputation** and will be recorded publicly."
            ),
            color=discord.Color.gold(),
        )
        await ctx.send(content=member.mention, embed=embed)

    # -----------------------------------------------------------------------
    # /accept  (target accepts a pending challenge)
    # -----------------------------------------------------------------------

    @commands.hybrid_command(name="accept", description="Accept a pending Dao Challenge against you")
    async def accept(self, ctx: commands.Context) -> None:
        pending = await get_incoming_challenge(ctx.author.id)
        if not pending:
            await ctx.send(
                embed=error_embed(ctx, "You have no pending challenges to accept."),
                ephemeral=True,
            )
            return

        challenger_id = pending["challenger_id"]
        challenger_member = ctx.guild.get_member(challenger_id)
        if challenger_member is None:
            await delete_challenge(challenger_id, ctx.author.id)
            await ctx.send(embed=error_embed(ctx, "The challenger is no longer in this server."), ephemeral=True)
            return

        c_row = await get_cultivator(challenger_id)
        t_row = await get_cultivator(ctx.author.id)

        if not c_row or not t_row:
            await ctx.send(embed=error_embed(ctx, "Cultivator data missing."), ephemeral=True)
            return

        await accept_challenge(challenger_id, ctx.author.id)
        await delete_challenge(challenger_id, ctx.author.id)

        challenger = _make_combatant(c_row)
        target     = _make_combatant(t_row)
        result     = resolve_combat(challenger, target)

        winner_row = c_row if result.challenger_won else t_row
        loser_row  = t_row if result.challenger_won else c_row
        winner_mem = challenger_member if result.challenger_won else ctx.author
        loser_mem  = ctx.author if result.challenger_won else challenger_member

        # Qi steal
        margin     = result.challenger_wins if result.challenger_won else result.target_wins
        steal      = qi_steal_amount(loser_row["qi"], result.challenger_won, margin)
        await transfer_qi(winner_row["discord_id"], loser_row["discord_id"], steal)

        # Reputation
        above = _above_realm(winner_row, loser_row)
        rep   = REP_WIN_ABOVE_REALM if above else REP_WIN_CHALLENGE
        await record_win(winner_row["discord_id"], rep)
        await record_loss(loser_row["discord_id"], 0)

        # Vendetta on loser → winner
        await log_combat(
            challenger_id=challenger_id,
            target_id=ctx.author.id,
            fight_type="challenge",
            outcome="challenger_win" if result.challenger_won else "target_win",
            qi_transferred=steal,
            vendetta_active=True,
        )

        extra = (
            f"💎 **{winner_mem.display_name}** steals `{steal}` Qi\n"
            f"📛 Vendetta placed on **{winner_mem.display_name}** by **{loser_mem.display_name}**\n"
            f"🏆 `+{rep}` reputation for **{winner_mem.display_name}**"
        )
        embed = _combat_embed(
            ctx,
            title="⚔️ Dao Challenge — Resolved",
            challenger=challenger_member,
            target=ctx.author,
            result=result,
            color=discord.Color.red(),
            extra_lines=extra,
        )
        await ctx.send(embed=embed)
        await self._pvp_log(embed)

    # -----------------------------------------------------------------------
    # /flee  (target flees a pending challenge)
    # -----------------------------------------------------------------------

    @commands.hybrid_command(name="flee", description="Flee a pending Dao Challenge (costs reputation)")
    async def flee(self, ctx: commands.Context) -> None:
        pending = await get_incoming_challenge(ctx.author.id)
        if not pending:
            await ctx.send(embed=error_embed(ctx, "You have no pending challenges to flee from."), ephemeral=True)
            return

        challenger_id = pending["challenger_id"]
        challenger_member = ctx.guild.get_member(challenger_id)

        await delete_challenge(challenger_id, ctx.author.id)
        await record_fled(ctx.author.id)

        await log_combat(
            challenger_id=challenger_id,
            target_id=ctx.author.id,
            fight_type="challenge",
            outcome="target_win",   # challenger "wins" by default
            qi_transferred=0,
            vendetta_active=False,
        )

        embed = build_embed(
            ctx,
            title="🏃 Challenge Fled",
            description=(
                f"**{ctx.author.display_name}** has fled from "
                f"{'**' + challenger_member.display_name + '**' if challenger_member else 'the challenger'}.\n\n"
                f"**-15 reputation** recorded against {ctx.author.mention}.\n"
                f"This cowardice has been noted in your profile."
            ),
            color=discord.Color.dark_gray(),
        )
        await ctx.send(embed=embed)

    # -----------------------------------------------------------------------
    # /duel @user  (request a Life-and-Death Duel)
    # -----------------------------------------------------------------------

    @commands.hybrid_command(name="duel", description="Issue a Life-and-Death Duel — loser loses a stage")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def duel(self, ctx: commands.Context, member: discord.Member) -> None:
        if member.id == ctx.author.id:
            await ctx.send(embed=error_embed(ctx, "You cannot duel yourself."), ephemeral=True)
            return

        c_row = await get_cultivator(ctx.author.id)
        t_row = await get_cultivator(member.id)

        if not c_row or not t_row:
            await ctx.send(embed=error_embed(ctx, "Both cultivators must be registered."), ephemeral=True)
            return

        # 7-day cooldown check
        now = datetime.now(timezone.utc)
        c_cd = await get_cooldown(ctx.author.id, "duel")
        t_cd = await get_cooldown(member.id, "duel")

        if c_cd and c_cd > now:
            delta = c_cd - now
            await ctx.send(
                embed=error_embed(ctx, f"You may not enter another Life-and-Death Duel for **{delta.days}d {delta.seconds//3600}h**."),
                ephemeral=True,
            )
            return

        if t_cd and t_cd > now:
            delta = t_cd - now
            await ctx.send(
                embed=error_embed(ctx, f"{member.display_name} cannot duel for another **{delta.days}d {delta.seconds//3600}h**."),
                ephemeral=True,
            )
            return

        existing = await get_duel_request(ctx.author.id, member.id)
        if existing:
            await ctx.send(embed=warning_embed(ctx, "You already have a pending duel request."), ephemeral=True)
            return

        expires = now + timedelta(seconds=DUEL_WINDOW_SECONDS)
        await create_duel_request(ctx.author.id, member.id, expires)

        embed = build_embed(
            ctx,
            title="☠️ Life-and-Death Duel Requested",
            description=(
                f"{ctx.author.mention} has challenged {member.mention} to a **Life-and-Death Duel**.\n\n"
                f"⚠️ The **loser will lose one stage** of cultivation.\n"
                f"The **winner gains Qi and a permanent foundation bonus**.\n\n"
                f"{member.mention} — use `/acceptduel` to agree or simply ignore to decline.\n"
                f"*(This request expires in 5 minutes)*"
            ),
            color=discord.Color.dark_red(),
        )
        await ctx.send(content=member.mention, embed=embed)

    # -----------------------------------------------------------------------
    # /acceptduel  (target accepts a Life-and-Death Duel)
    # -----------------------------------------------------------------------

    @commands.hybrid_command(name="acceptduel", description="Accept a pending Life-and-Death Duel")
    async def acceptduel(self, ctx: commands.Context) -> None:
        # Find any duel request targeting this user
        from db.pvp import get_incoming_challenge  # reuse pattern
        from db.database import fetch_one

        pending = await fetch_one(
            """
            SELECT * FROM pending_duels
            WHERE target_id = %s AND expires_at > %s AND accepted = FALSE
            ORDER BY requested_at DESC LIMIT 1
            """,
            (ctx.author.id, datetime.now(timezone.utc).replace(tzinfo=None)),
        )

        if not pending:
            await ctx.send(embed=error_embed(ctx, "You have no pending duel requests."), ephemeral=True)
            return

        challenger_id     = pending["challenger_id"]
        challenger_member = ctx.guild.get_member(challenger_id)
        if challenger_member is None:
            await delete_duel_request(challenger_id, ctx.author.id)
            await ctx.send(embed=error_embed(ctx, "The challenger has left the server."), ephemeral=True)
            return

        c_row = await get_cultivator(challenger_id)
        t_row = await get_cultivator(ctx.author.id)

        if not c_row or not t_row:
            await ctx.send(embed=error_embed(ctx, "Cultivator data missing."), ephemeral=True)
            return

        await accept_duel(challenger_id, ctx.author.id)
        await delete_duel_request(challenger_id, ctx.author.id)

        challenger = _make_combatant(c_row)
        target     = _make_combatant(t_row)
        result     = resolve_combat(challenger, target)

        winner_row = c_row if result.challenger_won else t_row
        loser_row  = t_row if result.challenger_won else c_row
        winner_mem = challenger_member if result.challenger_won else ctx.author
        loser_mem  = ctx.author if result.challenger_won else challenger_member

        # Stage loss for loser
        updated_loser = await apply_stage_loss(loser_row["discord_id"], loser_row)

        # Qi gain for winner (10% of loser's qi)
        qi_gain = max(1, int(loser_row["qi"] * 0.10))
        from db.cultivators import add_qi
        await add_qi(winner_row["discord_id"], qi_gain)

        # Permanent foundation bonus
        await apply_foundation_bonus(winner_row["discord_id"])

        # Reputation
        await record_win(winner_row["discord_id"], REP_WIN_DUEL)
        await record_loss(loser_row["discord_id"], 0)

        # 7-day cooldown for both
        now = datetime.now(timezone.utc)
        cd_expires = now + timedelta(days=DUEL_COOLDOWN_DAYS)
        await set_cooldown(challenger_id, "duel", cd_expires)
        await set_cooldown(ctx.author.id, "duel", cd_expires)

        await log_combat(
            challenger_id=challenger_id,
            target_id=ctx.author.id,
            fight_type="duel",
            outcome="challenger_win" if result.challenger_won else "target_win",
            qi_transferred=qi_gain,
        )

        stage_line = ""
        if updated_loser:
            stage_line = (
                f"\n💀 **{loser_mem.display_name}** falls to "
                f"**{updated_loser['realm'].replace('_',' ').title()} Stage {updated_loser['stage']}**"
            )
        else:
            stage_line = f"\n💀 **{loser_mem.display_name}** is already at the lowest stage — no further regression."

        extra = (
            f"🏆 **{winner_mem.display_name}** wins `+{qi_gain}` Qi & permanent foundation bonus\n"
            f"🌟 `+{REP_WIN_DUEL}` reputation"
            + stage_line
        )

        embed = _combat_embed(
            ctx,
            title="☠️ Life-and-Death Duel — Concluded",
            challenger=challenger_member,
            target=ctx.author,
            result=result,
            color=discord.Color.dark_red(),
            extra_lines=extra,
        )
        await ctx.send(embed=embed)
        await self._pvp_log(embed)

    # -----------------------------------------------------------------------
    # /ambush @user
    # -----------------------------------------------------------------------

    @commands.hybrid_command(name="ambush", description="Ambush a cultivator in closed cultivation")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ambush(self, ctx: commands.Context, member: discord.Member) -> None:
        if member.id == ctx.author.id:
            await ctx.send(embed=error_embed(ctx, "You cannot ambush yourself."), ephemeral=True)
            return

        # Attacker cooldown check
        now  = datetime.now(timezone.utc)
        a_cd = await get_cooldown(ctx.author.id, "ambush")
        if a_cd and a_cd > now:
            delta = a_cd - now
            hours = delta.seconds // 3600
            mins  = (delta.seconds % 3600) // 60
            await ctx.send(
                embed=error_embed(ctx, f"You are still recovering from your last ambush. **{hours}h {mins}m** remaining."),
                ephemeral=True,
            )
            return

        # Crippled check
        if await is_crippled(ctx.author.id):
            await ctx.send(
                embed=error_embed(ctx, "You are **Crippled** from a failed ambush and cannot attack."),
                ephemeral=True,
            )
            return

        c_row = await get_cultivator(ctx.author.id)
        t_row = await get_cultivator(member.id)

        if not c_row or not t_row:
            await ctx.send(embed=error_embed(ctx, "Both cultivators must be registered."), ephemeral=True)
            return

        # Target must be in closed cultivation
        closed_until = t_row.get("closed_cult_until")
        if closed_until is None:
            await ctx.send(
                embed=error_embed(ctx, f"**{member.display_name}** is not in closed cultivation. You cannot ambush them."),
                ephemeral=True,
            )
            return
        if closed_until.tzinfo is None:
            closed_until = closed_until.replace(tzinfo=timezone.utc)
        if closed_until <= now:
            await ctx.send(
                embed=error_embed(ctx, f"**{member.display_name}**'s closed cultivation has already ended."),
                ephemeral=True,
            )
            return

        # Ward check — +30% defensive power if warded
        warded = await has_active_ward(member.id)

        challenger = _make_combatant(c_row)
        target     = _make_combatant(t_row)

        # Apply ward as a pseudo power modifier — we do this by temporarily
        # boosting the target's stage for the roll inside a wrapper result
        if warded:
            # Manually resolve with +30% target power multiplier
            from combat.resolver import _roll_power, RoundResult, CombatResult
            rounds = []
            c_wins = 0
            t_wins = 0
            for _ in range(3):
                cp = _roll_power(challenger, target)
                tp = _roll_power(target, challenger) * 1.30
                c_won = cp > tp
                if c_won:
                    c_wins += 1
                else:
                    t_wins += 1
                rounds.append(RoundResult(challenger_power=cp, target_power=tp, challenger_won=c_won))
            result = CombatResult(
                challenger_won=c_wins > t_wins,
                rounds=rounds,
                challenger_wins=c_wins,
                target_wins=t_wins,
            )
        else:
            result = resolve_combat(challenger, target)

        ambush_cd_expires = now + timedelta(hours=AMBUSH_COOLDOWN_HOURS)

        if result.challenger_won:
            # Attacker wins — steal 30% Qi, break closed cultivation
            steal = max(1, int(t_row["qi"] * 0.30))
            await transfer_qi(c_row["discord_id"], t_row["discord_id"], steal)
            await clear_closed_cultivation(member.id)
            await set_cooldown(ctx.author.id, "ambush", ambush_cd_expires)
            await add_reputation(ctx.author.id, REP_AMBUSH_SUCCESS)

            await log_combat(
                challenger_id=ctx.author.id,
                target_id=member.id,
                fight_type="ambush",
                outcome="challenger_win",
                qi_transferred=steal,
            )

            extra = (
                f"💎 **{ctx.author.display_name}** steals `{steal}` Qi\n"
                f"🔓 **{member.display_name}**'s closed cultivation has been broken\n"
                f"{'🛡️ Ward was active but did not hold!' if warded else ''}"
            )
            color = discord.Color.dark_orange()
            title = "🗡️ Ambush — Success"
        else:
            # Attacker loses — Crippled debuff + public log
            crippled_until = now + timedelta(hours=AMBUSH_COOLDOWN_HOURS)
            await apply_crippled(ctx.author.id, crippled_until)
            await set_cooldown(ctx.author.id, "ambush", ambush_cd_expires)
            await add_reputation(ctx.author.id, REP_AMBUSH_FAIL)

            await log_combat(
                challenger_id=ctx.author.id,
                target_id=member.id,
                fight_type="ambush",
                outcome="target_win",
                qi_transferred=0,
            )

            extra = (
                f"💢 **{ctx.author.display_name}** is now **Crippled** for 48 hours\n"
                f"📛 This shameful defeat has been recorded on their profile\n"
                f"{'🛡️ Formation Ward absorbed the strike.' if warded else ''}"
            )
            color = discord.Color.dark_gray()
            title = "🗡️ Ambush — Repelled"

        embed = _combat_embed(
            ctx,
            title=title,
            challenger=ctx.author,
            target=member,
            result=result,
            color=color,
            extra_lines=extra,
        )
        await ctx.send(embed=embed)
        await self._pvp_log(embed)

    # -----------------------------------------------------------------------
    # /ward  (set formation ward before closed cultivation)
    # -----------------------------------------------------------------------

    @commands.hybrid_command(name="ward", description="Set a Formation Ward to defend against ambushes")
    @commands.cooldown(1, 3600, commands.BucketType.user)
    async def ward(self, ctx: commands.Context) -> None:
        row = await get_cultivator(ctx.author.id)
        if not row:
            await ctx.send(embed=error_embed(ctx, "You are not registered."), ephemeral=True)
            return

        # Warn if not in closed cultivation
        closed_until = row.get("closed_cult_until")
        now = datetime.now(timezone.utc)
        in_closed = (
            closed_until is not None
            and (closed_until if closed_until.tzinfo else closed_until.replace(tzinfo=timezone.utc)) > now
        )

        ward_expires = now + timedelta(hours=WARD_DURATION_HOURS)
        await set_ward(ctx.author.id, ward_expires)

        note = "" if in_closed else "\n\n⚠️ You are not currently in closed cultivation — the ward will still activate but you may want to `/closedcult` first."

        embed = build_embed(
            ctx,
            title="🛡️ Formation Ward Set",
            description=(
                f"Your **Formation Ward** is now active for the next **{WARD_DURATION_HOURS} hours**.\n"
                f"Any ambush attempt against you will face a **+30% defensive bonus**."
                + note
            ),
            color=discord.Color.teal(),
            show_footer=True,
        )
        await ctx.send(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # Internal log helper
    # -----------------------------------------------------------------------

    async def _pvp_log(self, embed: discord.Embed) -> None:
        channel = self.bot.get_channel(PVP_LOG_CHANNEL)
        if channel is None:
            return
        try:
            await channel.send(embed=embed)
        except Exception:
            log.warning("PvP » Could not send to PVP_LOG_CHANNEL")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PvP(bot))