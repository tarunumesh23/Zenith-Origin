from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from cultivation.breakthrough import attempt_breakthrough
from cultivation.constants import (
    AFFINITY_DISPLAY,
    AFFINITIES,
    AFFINITY_BREAKTHROUGH_MODIFIER,
    REALM_DISPLAY,
    QI_LIVE_UPDATE_INTERVAL,
    CLOSED_CULT_MULTIPLIER,
    compute_current_qi,
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
    """Fetch cultivator row and send error embed if not registered."""
    try:
        row = await db.get_cultivator(ctx.author.id)
    except Exception:
        log.exception("Cultivate » DB fetch failed for %s", ctx.author.id)
        await ctx.send(
            embed=error_embed(ctx, title="Database Error",
                              description="Could not fetch your profile. Try again later."),
            ephemeral=True,
        )
        return None

    if row is None:
        await ctx.send(
            embed=error_embed(ctx, title="Not a Cultivator",
                              description="You have not yet walked the Path. Use `z!start` to begin."),
            ephemeral=True,
        )
        return None

    return row


async def _check_closed_cultivation(ctx: commands.Context, row: dict) -> bool:
    """
    If the user is in closed cultivation, cancel it and notify them.
    Returns True if closed cultivation was active (and has been cancelled).
    """
    closed_until = row.get("closed_cult_until")
    if closed_until and _as_utc(closed_until) > _now():
        # Flush accrued Qi first so we don't lose progress silently
        await _flush_qi(row)
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


async def _flush_qi(row: dict, now: datetime | None = None) -> dict:
    """
    Compute how much Qi has accrued since last_updated, write it to the DB,
    and return the updated row.  Also enters tribulation if threshold is hit.
    """
    if now is None:
        now = _now()

    current_qi, _ = compute_current_qi(
        qi_stored=row["qi"],
        qi_threshold=row["qi_threshold"],
        last_updated=row.get("last_updated"),
        affinity=row.get("affinity"),
        closed_cult_until=row.get("closed_cult_until"),
        now=now,
    )
    current_qi = int(current_qi)

    # Persist the flushed value and stamp last_updated = now
    updated = await db.set_qi(row["discord_id"], current_qi, now)

    # Trigger tribulation if threshold reached
    if (
        updated["qi"] >= updated["qi_threshold"]
        and not updated.get("in_tribulation")
    ):
        await db.enter_tribulation(row["discord_id"])
        updated["in_tribulation"] = True

    return updated


# ---------------------------------------------------------------------------
# Live /qi embed builder
# ---------------------------------------------------------------------------

def _build_qi_embed(
    ctx_or_interaction,
    row: dict,
    now: datetime,
) -> discord.Embed:
    current_qi, rate = compute_current_qi(
        qi_stored=row["qi"],
        qi_threshold=row["qi_threshold"],
        last_updated=row.get("last_updated"),
        affinity=row.get("affinity"),
        closed_cult_until=row.get("closed_cult_until"),
        now=now,
    )
    current_qi = int(current_qi)
    threshold  = row["qi_threshold"]
    affinity   = row.get("affinity") or "none"
    realm      = row["realm"]
    stage      = row["stage"]
    in_trib    = row.get("in_tribulation", False)
    closed_until = row.get("closed_cult_until")

    # Progress bar (20 chars)
    pct    = min(current_qi / threshold, 1.0) if threshold else 0.0
    filled = int(pct * 20)
    bar    = "█" * filled + "░" * (20 - filled)

    # Time to fill
    qi_remaining = max(0, threshold - current_qi)
    if in_trib:
        ttf_str = "⚡ **Tribulation ready — use `/breakthrough`!**"
    elif qi_remaining == 0:
        ttf_str = "Full"
    elif rate > 0:
        secs  = qi_remaining / rate
        hours, rem   = divmod(int(secs), 3600)
        mins, secs_r = divmod(rem, 60)
        parts = []
        if hours:  parts.append(f"{hours}h")
        if mins:   parts.append(f"{mins}m")
        if secs_r: parts.append(f"{secs_r}s")
        ttf_str = " ".join(parts) if parts else "< 1s"
    else:
        ttf_str = "—"

    # Closed cultivation status
    if closed_until and _as_utc(closed_until) > now:
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
        f"**Qi/sec:** `{rate:.3f}`\n"
        f"**Time to fill:** {ttf_str}\n\n"
        f"**Closed Cultivation:** {cc_str}\n"
        f"**Tribulation:** {'⚡ Pending — use `/breakthrough`!' if in_trib else 'Not yet'}\n\n"
        f"-# Updates every {QI_LIVE_UPDATE_INTERVAL}s"
    )

    # Determine author name from context or interaction
    display_name = getattr(
        getattr(ctx_or_interaction, "author", None)
        or getattr(ctx_or_interaction, "user", None),
        "display_name", "Cultivator"
    )

    return build_embed(
        ctx_or_interaction,
        title=f"🔮 {display_name}'s Qi Status",
        description=desc,
        color=discord.Color.blurple(),
        show_footer=True,
        show_timestamp=True,
    )


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Cultivate(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /choose_affinity
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="choose_affinity",
                             description="Choose your elemental affinity (once only)")
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
                    description=(
                        f"Your path is already attuned to **{AFFINITY_DISPLAY[row['affinity']]}**. "
                        "This cannot be changed."
                    ),
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
                "🔥 **Fire** — +15% Qi/sec. Breakthroughs are unstable but powerful.\n"
                "💧 **Water** — Smooth progression. Breakthroughs are safer.\n"
                "⚡ **Lightning** — Volatile. Hardest breakthroughs, but rare stage skips.\n"
                "🌿 **Wood** — +10% Qi/sec. Steady and resilient.\n"
                "🪨 **Earth** — Tanky. Slower Qi gain, best combat stability."
            ),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # /qi  —  live Qi progress (updates every QI_LIVE_UPDATE_INTERVAL sec)
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="qi",
                             description="Check your current Qi progress (live, updates every 5s)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def qi(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer()

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        now = _now()
        embed = _build_qi_embed(ctx, row, now)
        message = await ctx.send(embed=embed)

        # Live-update loop — runs for 60 seconds (12 × 5 s ticks)
        for _ in range(12):
            await asyncio.sleep(QI_LIVE_UPDATE_INTERVAL)

            # Re-fetch row so closed-cult and tribulation flags are current
            try:
                row = await db.get_cultivator(ctx.author.id)
            except Exception:
                break
            if row is None:
                break

            # Check if tribulation was just entered on the server side
            now = _now()
            current_qi, _ = compute_current_qi(
                qi_stored=row["qi"],
                qi_threshold=row["qi_threshold"],
                last_updated=row.get("last_updated"),
                affinity=row.get("affinity"),
                closed_cult_until=row.get("closed_cult_until"),
                now=now,
            )

            # Auto-enter tribulation if threshold hit and not yet flagged
            if (
                int(current_qi) >= row["qi_threshold"]
                and not row.get("in_tribulation")
            ):
                await db.enter_tribulation(row["discord_id"])
                row["in_tribulation"] = True

                user = self.bot.get_user(ctx.author.id)
                if user:
                    try:
                        await user.send(
                            "⚡ **Tribulation Approaches.** Your Qi has reached its limit. "
                            "Use `/breakthrough` before your energy destabilises."
                        )
                    except discord.Forbidden:
                        pass

            try:
                await message.edit(embed=_build_qi_embed(ctx, row, now))
            except discord.NotFound:
                break
            except discord.HTTPException:
                break

    # ------------------------------------------------------------------
    # /meditate
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="meditate",
                             description="Meditate for a burst of Qi (1h cooldown)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def meditate(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer()

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        if row["affinity"] is None:
            await ctx.send(
                embed=error_embed(ctx, title="No Affinity",
                                  description="Choose your affinity first with `/choose_affinity`."),
                ephemeral=True,
            )
            return

        if await _check_closed_cultivation(ctx, row):
            return

        expires = await _check_cooldown(ctx.author.id, "meditate")
        if expires:
            await ctx.send(
                embed=error_embed(ctx, title="Still Recovering",
                                  description=f"You may meditate again in **{_format_cooldown(expires)}**."),
                ephemeral=True,
            )
            return

        if row["in_tribulation"]:
            await ctx.send(
                embed=error_embed(ctx, title="In Tribulation",
                                  description="Your Qi is at its limit. Attempt your breakthrough before meditating."),
                ephemeral=True,
            )
            return

        # Flush accrued Qi first so we add on top of the real current value
        now     = _now()
        updated = await _flush_qi(row, now)

        # Meditate bonus: 90 seconds worth of Qi at current rate (1.5× one tick)
        _, rate  = compute_current_qi(
            qi_stored=updated["qi"],
            qi_threshold=updated["qi_threshold"],
            last_updated=now,          # just flushed
            affinity=row.get("affinity"),
            closed_cult_until=row.get("closed_cult_until"),
            now=now,
        )
        qi_gain = max(1, int(rate * 90))   # ~1.5× a 60-second window

        updated = await db.add_qi(ctx.author.id, qi_gain)
        await db.set_cooldown(ctx.author.id, "meditate", now + timedelta(hours=1))

        entered_tribulation = False
        if updated["qi"] >= updated["qi_threshold"] and not row["in_tribulation"]:
            await db.enter_tribulation(ctx.author.id)
            entered_tribulation = True

        desc = (
            f"You sink into stillness. The world fades.\n\n"
            f"**+{qi_gain} Qi** absorbed.\n"
            f"Current Qi: `{updated['qi']:,} / {updated['qi_threshold']:,}`"
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
                             description="Enter closed cultivation for 4h — 2× Qi rate, but you're vulnerable")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def closed_cultivation(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.defer()

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        if row["affinity"] is None:
            await ctx.send(
                embed=error_embed(ctx, title="No Affinity",
                                  description="Choose your affinity first with `/choose_affinity`."),
                ephemeral=True,
            )
            return

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

        expires = await _check_cooldown(ctx.author.id, "closed_cultivation")
        if expires:
            await ctx.send(
                embed=error_embed(ctx, title="Recovering",
                                  description=f"You may enter closed cultivation again in **{_format_cooldown(expires)}**."),
                ephemeral=True,
            )
            return

        # Flush Qi at current (non-boosted) rate before starting the 2× window
        await _flush_qi(row)

        until = _now() + timedelta(hours=4)
        await db.set_closed_cultivation(ctx.author.id, until)
        await db.set_cooldown(ctx.author.id, "closed_cultivation", until)

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🔒 Closed Cultivation Begun",
                description=(
                    "You seal yourself away from the world.\n\n"
                    "**2× Qi rate** for the next **4 hours**.\n"
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

        if await _check_closed_cultivation(ctx, row):
            return

        if row["stabilise_used"]:
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="Already Used",
                    description=(
                        "You have already stabilised your foundation this realm. "
                        "It resets when you advance to the next realm."
                    ),
                ),
                ephemeral=True,
            )
            return

        if not row["in_tribulation"]:
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="Not in Tribulation",
                    description=(
                        "You can only stabilise when your Qi has reached its threshold "
                        "and tribulation is imminent."
                    ),
                ),
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
            await ctx.send(
                embed=error_embed(ctx, title="No Affinity",
                                  description="Choose your affinity first with `/choose_affinity`."),
                ephemeral=True,
            )
            return

        if await _check_closed_cultivation(ctx, row):
            return

        # Flush Qi so in_tribulation is accurate before we check it
        row = await _flush_qi(row)

        if not row["in_tribulation"]:
            # Compute display qi for the error message
            current_qi, _ = compute_current_qi(
                qi_stored=row["qi"],
                qi_threshold=row["qi_threshold"],
                last_updated=row.get("last_updated"),
                affinity=row.get("affinity"),
                closed_cult_until=row.get("closed_cult_until"),
            )
            qi_needed = row["qi_threshold"] - int(current_qi)
            await ctx.send(
                embed=error_embed(
                    ctx,
                    title="Not Ready",
                    description=(
                        f"Your Qi has not reached its threshold.\n\n"
                        f"Current: `{int(current_qi):,} / {row['qi_threshold']:,}`\n"
                        f"Still need: `{max(0, qi_needed):,}` Qi"
                    ),
                ),
                ephemeral=True,
            )
            return

        expires = await _check_cooldown(ctx.author.id, "breakthrough")
        if expires:
            await ctx.send(
                embed=error_embed(ctx, title="Meridians Still Recovering",
                                  description=f"You may attempt again in **{_format_cooldown(expires)}**."),
                ephemeral=True,
            )
            return

        result = await attempt_breakthrough(row)

        if result.cooldown_end:
            await db.set_cooldown(ctx.author.id, "breakthrough", result.cooldown_end)

        if result.outcome == "success":
            color = discord.Color.gold() if result.overflow else discord.Color.green()
            title = "⚡ Qi Overflow — Double Advance!" if result.overflow else "✅ Breakthrough Success"
        else:
            color = discord.Color.red()
            title = "❌ Breakthrough Failed"

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
            fields.append({"name": "Qi Lost", "value": f"`{result.qi_lost}`", "inline": True})
        if result.cooldown_end:
            fields.append({
                "name": "Next Attempt",
                "value": f"<t:{int(result.cooldown_end.timestamp())}:R>",
                "inline": True,
            })

        await ctx.send(
            embed=build_embed(
                ctx,
                title=title,
                description=result.message,
                color=color,
                fields=fields,
                show_footer=True,
                show_timestamp=True,
            )
        )

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
            interaction,          # type: ignore[arg-type]
            title="⚗️ Affinity Chosen",
            description=(
                f"Your soul resonates with **{AFFINITY_DISPLAY[self.affinity]}**.\n\n"
                "This is your Path. Walk it well."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.view.stop()          # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Cultivate(bot))