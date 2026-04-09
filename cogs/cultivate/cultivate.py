from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from cultivation.breakthrough import attempt_breakthrough
from cultivation.constants import (
    AFFINITY_DISPLAY,
    AFFINITY_QI_MULTIPLIER,
    AFFINITIES,
    BASE_QI_PER_TICK,
    CLOSED_CULT_MULTIPLIER,
    REALM_DISPLAY,
    TICK_INTERVAL_SECONDS,
    get_reputation_title,
)
from db import cultivators as db
from ui.embed import build_embed, error_embed

log = logging.getLogger("bot.cogs.cultivate")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _format_cooldown(expires: datetime) -> str:
    remaining = int((_as_utc(expires) - _now()).total_seconds())
    if remaining <= 0:
        return "ready"
    minutes, seconds = divmod(remaining, 60)
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


async def _check_cooldown(discord_id: int, command: str) -> datetime | None:
    """Return expiry datetime if on cooldown, else None."""
    expires = await db.get_cooldown(discord_id, command)
    if expires and _as_utc(expires) > _now():
        return expires
    return None


async def _guard_cultivator(ctx: commands.Context) -> dict | None:
    """Fetch cultivator row and send error embed if not registered. Returns row or None."""
    try:
        row = await db.get_cultivator(ctx.author.id)
    except Exception:
        log.exception("Cultivate » DB fetch failed for %s", ctx.author.id)
        await ctx.send(embed=error_embed(ctx, title="Database Error",
                                         description="Could not fetch your profile. Try again later."),
                       ephemeral=True)
        return None

    if row is None:
        await ctx.send(embed=error_embed(ctx, title="Not a Cultivator",
                                         description="You have not yet walked the Path. Use `z!start` to begin."),
                       ephemeral=True)
        return None

    return row


async def _check_closed_cultivation(ctx: commands.Context, row: dict) -> bool:
    """
    If the user is in closed cultivation, cancel it and notify them.
    Returns True if closed cultivation was active (and has been cancelled).
    Returns False if they were not in closed cultivation.
    """
    closed_until = row.get("closed_cult_until")
    if closed_until and _as_utc(closed_until) > _now():
        await db.clear_closed_cultivation(ctx.author.id)
        await ctx.send(
            embed=build_embed(
                ctx,
                title="🔓 Closed Cultivation Broken",
                description=(
                    "You stirred from your seclusion.\n\n"
                    "Your **closed cultivation has been cancelled** and the **2× Qi bonus is lost**.\n"
                    "Be more mindful next time you enter seclusion."
                ),
                color=discord.Color.dark_orange(),
                show_footer=True,
            )
        )
        return True
    return False


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Cultivate(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.passive_tick.start()

    def cog_unload(self) -> None:
        self.passive_tick.cancel()

    # ------------------------------------------------------------------
    # Passive tick loop
    # ------------------------------------------------------------------

    @tasks.loop(seconds=TICK_INTERVAL_SECONDS)
    async def passive_tick(self) -> None:
        """Award passive Qi to all cultivators every tick interval."""
        try:
            cultivators = await db.get_all_cultivators()
        except Exception:
            log.exception("Passive tick » Failed to fetch cultivators")
            return

        now = _now()
        for row in cultivators:
            try:
                await self._process_tick(row, now)
            except Exception:
                log.exception("Passive tick » Failed for discord_id=%s", row["discord_id"])

    @passive_tick.before_loop
    async def before_tick(self) -> None:
        await self.bot.wait_until_ready()

    async def _process_tick(self, row: dict, now: datetime) -> None:
        discord_id = row["discord_id"]
        affinity   = row["affinity"] or "water"

        # Skip if in tribulation (Qi is full / locked)
        if row["in_tribulation"]:
            return

        # Qi multiplier from affinity
        multiplier = AFFINITY_QI_MULTIPLIER.get(affinity, 1.0)

        # Double if in closed cultivation and buff hasn't expired
        closed_until = row["closed_cult_until"]
        if closed_until and _as_utc(closed_until) > now:
            multiplier *= CLOSED_CULT_MULTIPLIER
        elif closed_until and _as_utc(closed_until) <= now:
            # Buff expired — clear it
            await db.clear_closed_cultivation(discord_id)

        qi_gain = max(1, int(BASE_QI_PER_TICK * multiplier))
        updated = await db.add_qi(discord_id, qi_gain)
        await db.update_tick(discord_id)

        # Check if Qi just hit the threshold → enter tribulation
        if updated["qi"] >= updated["qi_threshold"] and not updated["in_tribulation"]:
            await db.enter_tribulation(discord_id)
            log.info("Passive tick » discord_id=%s entered tribulation", discord_id)

            # DM the cultivator
            user = self.bot.get_user(discord_id)
            if user:
                try:
                    await user.send(
                        "⚡ **Tribulation Approaches.** Your Qi has reached its limit. "
                        "Use `/breakthrough` within 24 hours or your energy will begin to destabilise."
                    )
                except discord.Forbidden:
                    pass

    # ------------------------------------------------------------------
    # /choose_affinity
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="choose_affinity", description="Choose your elemental affinity (once only)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def choose_affinity(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        if row["affinity"] is not None:
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="Affinity Already Chosen",
                    description=f"Your path is already attuned to **{AFFINITY_DISPLAY[row['affinity']]}**. This cannot be changed.",
                ),
                ephemeral=True,
            )
            return

        view = _AffinitySelectView(ctx, row)
        embed = build_embed(
            ctx,
            title="⚗️ Choose Your Elemental Affinity",
            description=(
                "Your affinity shapes every aspect of your cultivation.\n"
                "It affects your Qi gain, breakthrough odds, and combat power.\n\n"
                "**This choice is permanent.**\n\n"
                "🔥 **Fire** — +15% Qi gain. Breakthroughs are unstable but powerful.\n"
                "💧 **Water** — Smooth progression. Breakthroughs are safer.\n"
                "⚡ **Lightning** — Volatile. Hardest breakthroughs, but rare stage skips.\n"
                "🌿 **Wood** — +10% Qi gain. Steady and resilient.\n"
                "🪨 **Earth** — Tanky. Slower Qi gain, best combat stability."
            ),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # /qi  —  Qi progress overview
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="qi", description="Check your current Qi progress and cultivation status")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def qi(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer()

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        current_qi   = row["qi"]
        threshold    = row["qi_threshold"]
        affinity     = row["affinity"] or "none"
        realm        = row["realm"]
        stage        = row["stage"]
        in_trib      = row["in_tribulation"]
        closed_until = row["closed_cult_until"]

        # Progress bar (20 chars wide)
        pct    = min(current_qi / threshold, 1.0) if threshold else 0
        filled = int(pct * 20)
        bar    = "█" * filled + "░" * (20 - filled)

        # Estimate time-to-fill based on passive tick rate
        multiplier = AFFINITY_QI_MULTIPLIER.get(affinity, 1.0)
        if closed_until and _as_utc(closed_until) > _now():
            multiplier *= CLOSED_CULT_MULTIPLIER
        qi_per_tick  = max(1, int(BASE_QI_PER_TICK * multiplier))
        qi_remaining = max(0, threshold - current_qi)

        if in_trib:
            ttf_str = "⚡ **Tribulation ready — use `/breakthrough`!**"
        elif qi_remaining == 0:
            ttf_str = "Full"
        elif qi_per_tick > 0:
            ticks_needed   = (qi_remaining + qi_per_tick - 1) // qi_per_tick
            seconds_needed = ticks_needed * TICK_INTERVAL_SECONDS
            hours, rem     = divmod(seconds_needed, 3600)
            minutes, secs  = divmod(rem, 60)
            parts = []
            if hours:   parts.append(f"{hours}h")
            if minutes: parts.append(f"{minutes}m")
            if secs:    parts.append(f"{secs}s")
            ttf_str = " ".join(parts) if parts else "< 1s"
        else:
            ttf_str = "—"

        # Closed cultivation status
        if closed_until and _as_utc(closed_until) > _now():
            cc_str = f"Active — ends <t:{int(_as_utc(closed_until).timestamp())}:R>"
        else:
            cc_str = "Inactive"

        affinity_label = AFFINITY_DISPLAY.get(affinity, affinity.title()) if affinity != "none" else "Not chosen"
        realm_label    = REALM_DISPLAY.get(realm, realm) if realm else "Unknown"

        desc = (
            f"**Realm:** {realm_label} — Stage {stage}\n"
            f"**Affinity:** {affinity_label}\n\n"
            f"`{bar}` **{pct * 100:.1f}%**\n"
            f"**Qi:** `{current_qi:,} / {threshold:,}`\n"
            f"**Qi per tick:** `{qi_per_tick}`\n"
            f"**Time to fill:** {ttf_str}\n\n"
            f"**Closed Cultivation:** {cc_str}\n"
            f"**Tribulation:** {'⚡ Pending — use `/breakthrough`!' if in_trib else 'Not yet'}"
        )

        await ctx.send(
            embed=build_embed(
                ctx,
                title=f"🔮 {ctx.author.display_name}'s Qi Status",
                description=desc,
                color=discord.Color.blurple(),
                show_footer=True,
                show_timestamp=True,
            )
        )

    # ------------------------------------------------------------------
    # /meditate
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="meditate", description="Meditate for a small burst of Qi (1h cooldown)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def meditate(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer()

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        if row["affinity"] is None:
            await ctx.send(embed=error_embed(ctx, title="No Affinity",
                                             description="Choose your affinity first with `/choose_affinity`."),
                           ephemeral=True)
            return

        # Cancel closed cultivation if active, then stop
        if await _check_closed_cultivation(ctx, row):
            return

        # Cooldown check
        expires = await _check_cooldown(ctx.author.id, "meditate")
        if expires:
            await ctx.send(
                embed=error_embed(ctx, title="Still Recovering",
                                  description=f"You may meditate again in **{_format_cooldown(expires)}**."),
                ephemeral=True,
            )
            return

        # Can't meditate while in tribulation
        if row["in_tribulation"]:
            await ctx.send(
                embed=error_embed(ctx, title="In Tribulation",
                                  description="Your Qi is at its limit. Attempt your breakthrough before meditating."),
                ephemeral=True,
            )
            return

        affinity   = row["affinity"]
        multiplier = AFFINITY_QI_MULTIPLIER.get(affinity, 1.0)
        qi_gain    = max(1, int(BASE_QI_PER_TICK * 1.5 * multiplier))  # 1.5x a normal tick

        updated = await db.add_qi(ctx.author.id, qi_gain)
        await db.set_cooldown(ctx.author.id, "meditate", _now() + timedelta(hours=1))

        # Check if this push hit the threshold
        entered_tribulation = False
        if updated["qi"] >= updated["qi_threshold"] and not row["in_tribulation"]:
            await db.enter_tribulation(ctx.author.id)
            entered_tribulation = True

        desc = (
            f"You sink into stillness. The world fades.\n\n"
            f"**+{qi_gain} Qi** absorbed.\n"
            f"Current Qi: `{updated['qi']} / {updated['qi_threshold']}`"
        )
        if entered_tribulation:
            desc += "\n\n⚡ **Your Qi has reached its limit. Tribulation approaches — use `/breakthrough`.**"

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🧘 Meditation Complete",
                description=desc,
                color=discord.Color.teal(),
                show_footer=True,
            )
        )

    # ------------------------------------------------------------------
    # /closed_cultivation
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="closed_cultivation",
                             description="Enter closed cultivation for 4h — 2x Qi gain, but you're vulnerable")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def closed_cultivation(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer()

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        if row["affinity"] is None:
            await ctx.send(embed=error_embed(ctx, title="No Affinity",
                                             description="Choose your affinity first with `/choose_affinity`."),
                           ephemeral=True)
            return

        # Already in closed cultivation?
        if row["closed_cult_until"] and _as_utc(row["closed_cult_until"]) > _now():
            remaining = _format_cooldown(row["closed_cult_until"])
            await ctx.send(
                embed=error_embed(ctx, title="Already in Closed Cultivation",
                                  description=f"You emerge in **{remaining}**."),
                ephemeral=True,
            )
            return

        if row["in_tribulation"]:
            await ctx.send(
                embed=error_embed(ctx, title="In Tribulation",
                                  description="You cannot enter closed cultivation while tribulation looms."),
                ephemeral=True,
            )
            return

        # Cooldown check
        expires = await _check_cooldown(ctx.author.id, "closed_cultivation")
        if expires:
            await ctx.send(
                embed=error_embed(ctx, title="Recovering",
                                  description=f"You may enter closed cultivation again in **{_format_cooldown(expires)}**."),
                ephemeral=True,
            )
            return

        until = _now() + timedelta(hours=4)
        await db.set_closed_cultivation(ctx.author.id, until)
        await db.set_cooldown(ctx.author.id, "closed_cultivation", until)

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🔒 Closed Cultivation Begun",
                description=(
                    "You seal yourself away from the world.\n\n"
                    "**2× Qi gain** for the next **4 hours**.\n"
                    "⚠️ Using **any command** will **break your seclusion** and cancel the bonus.\n"
                    "⚠️ You are **vulnerable to ambush** while in seclusion.\n\n"
                    f"You will emerge at <t:{int(until.timestamp())}:t>."
                ),
                color=discord.Color.dark_purple(),
                show_footer=True,
            )
        )

    # ------------------------------------------------------------------
    # /stabilise
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="stabilise",
                             description="Reinforce your foundation before breakthrough (+10% success, once per realm)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def stabilise(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer()

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        # Cancel closed cultivation if active, then stop
        if await _check_closed_cultivation(ctx, row):
            return

        if row["stabilise_used"]:
            await ctx.send(
                embed=error_embed(ctx, title="Already Used",
                                  description="You have already stabilised your foundation this realm. "
                                              "It resets when you advance to the next realm."),
                ephemeral=True,
            )
            return

        if not row["in_tribulation"]:
            await ctx.send(
                embed=error_embed(ctx, title="Not in Tribulation",
                                  description="You can only stabilise when your Qi has reached its threshold "
                                              "and tribulation is imminent."),
                ephemeral=True,
            )
            return

        await db.use_stabilise(ctx.author.id)

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🛡️ Foundation Stabilised",
                description=(
                    "You draw your scattered thoughts inward and anchor your core.\n\n"
                    "**+10% breakthrough success chance** applied to your next attempt.\n"
                    "This bonus is consumed on your next `/breakthrough`."
                ),
                color=discord.Color.green(),
                show_footer=True,
            )
        )

    # ------------------------------------------------------------------
    # /breakthrough
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="breakthrough",
                             description="Attempt to break through to the next stage")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def breakthrough(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer()

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        if row["affinity"] is None:
            await ctx.send(embed=error_embed(ctx, title="No Affinity",
                                             description="Choose your affinity first with `/choose_affinity`."),
                           ephemeral=True)
            return

        # Cancel closed cultivation if active, then stop
        if await _check_closed_cultivation(ctx, row):
            return

        # Must be in tribulation
        if not row["in_tribulation"]:
            qi_needed = row["qi_threshold"] - row["qi"]
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="Not Ready",
                    description=(
                        f"Your Qi has not reached its threshold.\n\n"
                        f"Current: `{row['qi']} / {row['qi_threshold']}`\n"
                        f"Still need: `{qi_needed}` Qi"
                    ),
                ),
                ephemeral=True,
            )
            return

        # Cooldown check (from a recent failed attempt)
        expires = await _check_cooldown(ctx.author.id, "breakthrough")
        if expires:
            await ctx.send(
                embed=error_embed(ctx, title="Meridians Still Recovering",
                                  description=f"You may attempt again in **{_format_cooldown(expires)}**."),
                ephemeral=True,
            )
            return

        # Resolve
        result = await attempt_breakthrough(row)

        # If cooldown was set by the attempt, stamp it
        if result.cooldown_end:
            await db.set_cooldown(ctx.author.id, "breakthrough", result.cooldown_end)

        # Build embed
        if result.outcome == "success":
            color = discord.Color.gold() if result.overflow else discord.Color.green()
            title = "⚡ Qi Overflow — Double Advance!" if result.overflow else "✅ Breakthrough Success"
        elif result.outcome == "minor_fail":
            color = discord.Color.orange()
            title = "⚠️ Breakthrough Failed"
        else:
            color = discord.Color.red()
            title = "❌ Major Failure"

        fields = [
            {
                "name": "Before",
                "value": f"`{REALM_DISPLAY[result.realm_before]} Stage {result.stage_before}`",
                "inline": True,
            },
            {
                "name": "After",
                "value": f"`{REALM_DISPLAY[result.realm_after]} Stage {result.stage_after}`",
                "inline": True,
            },
        ]

        if result.qi_lost:
            fields.append({
                "name": "Qi Lost",
                "value": f"`{result.qi_lost}`",
                "inline": True,
            })

        if result.cooldown_end:
            fields.append({
                "name": "Next Attempt",
                "value": f"<t:{int(result.cooldown_end.timestamp())}:R>",
                "inline": True,
            })

        embed = build_embed(
            ctx,
            title=title,
            description=result.message,
            color=color,
            fields=fields,
            show_footer=True,
            show_timestamp=True,
        )

        await ctx.send(embed=embed)

        # Announce Qi Overflow publicly
        if result.overflow:
            log.info(
                "Breakthrough » Qi Overflow! discord_id=%s %s S%d → %s S%d",
                ctx.author.id,
                result.realm_before, result.stage_before,
                result.realm_after, result.stage_after,
            )


# ---------------------------------------------------------------------------
# Affinity selection view
# ---------------------------------------------------------------------------

class _AffinitySelectView(discord.ui.View):
    def __init__(self, ctx: commands.Context, row: dict) -> None:
        super().__init__(timeout=60)
        self.ctx = ctx
        self.row = row

        for affinity in AFFINITIES:
            self.add_item(_AffinityButton(affinity))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id


class _AffinityButton(discord.ui.Button):
    def __init__(self, affinity: str) -> None:
        super().__init__(
            label=AFFINITY_DISPLAY[affinity],
            style=discord.ButtonStyle.secondary,
            custom_id=f"affinity_{affinity}",
        )
        self.affinity = affinity

    async def callback(self, interaction: discord.Interaction) -> None:
        await db.set_affinity(interaction.user.id, self.affinity)

        embed = build_embed(
            interaction,  # type: ignore[arg-type]
            title="⚗️ Affinity Chosen",
            description=(
                f"Your soul resonates with **{AFFINITY_DISPLAY[self.affinity]}**.\n\n"
                "This is your Path. Walk it well."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.view.stop()  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Cultivate(bot))