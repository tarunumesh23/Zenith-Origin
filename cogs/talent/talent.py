from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

from talent.constants import (
    RARITIES,
    RARITY_ORDER,
    ONE_PER_SERVER_TALENTS,
    SPIN_PITY,
    FUSION_PITY,
)
from talent.engine import (
    spin_talent,
    fuse_talents,
    evolve_talent,
    accept_talent,
    reject_talent,
    toggle_lock,
)
from talent.cultivation_bridge import describe_bonuses
from talent.models import PlayerTalent, PlayerTalentData
from db import talent as db
from ui.embed import build_embed, error_embed
from ui.interaction_utils import safe_defer, safe_edit

log = logging.getLogger("bot.cogs.talent")

INVENTORY_MAX = 20
GIFT_MAX = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rarity_color(rarity: str) -> discord.Color:
    return discord.Color(RARITIES.get(rarity, {}).get("color", 0xFFFFFF))


def _enrich_talent(talent: PlayerTalent) -> PlayerTalent:
    rarity_data   = RARITIES.get(talent.rarity, {})
    talent.color  = rarity_data.get("color", 0xFFFFFF)
    talent.emoji  = rarity_data.get("emoji", "")
    return talent


async def _guard_cultivator(ctx: commands.Context) -> dict | None:
    try:
        from db import cultivators as cult_db
        row = await cult_db.get_cultivator(ctx.author.id)
    except Exception:
        log.exception("Talent » DB fetch failed for discord_id=%s", ctx.author.id)
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


async def _load_player(discord_id: int, guild_id: int) -> PlayerTalentData:
    raw = await db.get_player_talent_data(discord_id, guild_id)
    return raw if raw is not None else PlayerTalentData(user_id=discord_id, guild_id=guild_id)


def _talent_embed(
    ctx_or_interaction,
    talent: PlayerTalent,
    title: str,
    extra_desc: str = "",
    show_footer: bool = True,
) -> discord.Embed:
    talent      = _enrich_talent(talent)
    rarity_data = RARITIES.get(talent.rarity, {})
    stage_label = ["", " ✦", " ✦✦"][talent.evolution_stage]

    is_cosmic = talent.rarity == "Cosmic"
    flags_parts = []
    if talent.is_locked:
        flags_parts.append("🔒 Locked")
    if talent.is_corrupted:
        flags_parts.append("☠️ Corrupted")
    if is_cosmic:
        flags_parts.append("🌌 Cosmic Tier")
    flags = "  ".join(flags_parts)

    exclusive_note = ""
    if talent.is_corrupted:
        exclusive_note = "\n*[Corruption Exclusive]*"
    elif is_cosmic:
        exclusive_note = "\n*[Cosmic Exclusive — Unreachable by normal means]*"

    desc = (
        f"{talent.emoji} **{talent.name}**{stage_label}\n"
        f"**Rarity:** {rarity_data.get('emoji', '')} {talent.rarity}\n"
        f"**Multiplier:** ×{talent.multiplier:.2f}\n"
        f"**Tags:** {', '.join(talent.tags) if talent.tags else '—'}\n\n"
        f"*{talent.description}*"
        f"{exclusive_note}"
    )
    if flags:
        desc += f"\n\n{flags}"
    if extra_desc:
        desc += f"\n\n{extra_desc}"

    return build_embed(
        ctx_or_interaction,
        title=title,
        description=desc,
        color=_rarity_color(talent.rarity),
        show_footer=show_footer,
        show_timestamp=True,
    )


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Talent(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /talent ───────────────────────────────────────────────────────────

    @commands.hybrid_command(name="talent", description="View your active talent")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def talent(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction, ephemeral=True)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(ctx.author.id, guild_id)

        if player.active_talent is None:
            await ctx.send(
                embed=error_embed(ctx, title="No Active Talent",
                                  description="You have no talent yet. Use `/use_spin` if you have tokens."),
                ephemeral=True,
            )
            return

        pity_lines = "\n".join(
            f"**{tier}:** {player.spin_pity.get(tier, 0)} / {threshold}"
            for tier, threshold in SPIN_PITY.items()
        )
        cult_bonuses = describe_bonuses(player.active_talent)
        extra = (
            f"**Spin Pity**\n{pity_lines}\n**Total Spins:** {player.total_spins}"
            f"\n\n**✨ Cultivation Bonuses**\n{cult_bonuses}"
        )
        await ctx.send(
            embed=_talent_embed(
                ctx, player.active_talent,
                title=f"🌟 {ctx.author.display_name}'s Talent",
                extra_desc=extra,
            ),
            ephemeral=True,
        )

    # ── /inventory ────────────────────────────────────────────────────────

    @commands.hybrid_command(name="inventory", description="Browse your talent inventory")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def inventory(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction, ephemeral=True)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(ctx.author.id, guild_id)

        if not player.inventory:
            await ctx.send(
                embed=error_embed(ctx, title="Empty Inventory",
                                  description="Your inventory is empty."),
                ephemeral=True,
            )
            return

        lines = []
        for i, t in enumerate(player.inventory, start=1):
            t           = _enrich_talent(t)
            stage_label = ["", " ✦", " ✦✦"][t.evolution_stage]
            lock        = "🔒" if t.is_locked    else ""
            corrupt     = "☠️" if t.is_corrupted else ""
            cosmic      = "🌌" if t.rarity == "Cosmic" else ""
            lines.append(
                f"`{i:>2}.` {t.emoji} **{t.name}**{stage_label} "
                f"[{t.rarity}] ×{t.multiplier:.2f} {lock}{corrupt}{cosmic}"
            )

        desc  = "\n".join(lines)
        desc += f"\n\n`{len(player.inventory)} / {INVENTORY_MAX}` slots used"

        await ctx.send(
            embed=build_embed(
                ctx,
                title=f"🎒 {ctx.author.display_name}'s Inventory",
                description=desc,
                color=discord.Color.blurple(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    # ── /fuse ─────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="fuse", description="Fuse two talents from your inventory")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fuse(
        self,
        ctx: commands.Context,
        slot_a: int,
        slot_b: int,
        mode: str = "auto",
    ) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(ctx.author.id, guild_id)
        inv      = player.inventory

        if not (1 <= slot_a <= len(inv)) or not (1 <= slot_b <= len(inv)):
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Slot",
                                  description=f"You have **{len(inv)}** talents in inventory. "
                                              f"Choose valid slot numbers."),
                ephemeral=True,
            )
            return

        if slot_a == slot_b:
            await ctx.send(
                embed=error_embed(ctx, title="Same Slot",
                                  description="You cannot fuse a talent with itself."),
                ephemeral=True,
            )
            return

        talent_a = inv[slot_a - 1]
        talent_b = inv[slot_b - 1]

        if talent_a.is_locked or talent_b.is_locked:
            locked = talent_a.name if talent_a.is_locked else talent_b.name
            await ctx.send(
                embed=error_embed(ctx, title="Talent Locked",
                                  description=f"**{locked}** is locked. Unlock it first with `/lock`."),
                ephemeral=True,
            )
            return

        if mode not in ("auto", "same", "cross", "rng"):
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Mode",
                                  description="Mode must be: `auto`, `same`, `cross`, or `rng`."),
                ephemeral=True,
            )
            return

        result = fuse_talents(player, talent_a, talent_b, mode=mode)

        for idx in sorted([slot_a - 1, slot_b - 1], reverse=True):
            player.inventory.pop(idx)

        if result["success"]:
            result_talent = result["result_talent"]
            if result_talent:
                player.inventory.append(result_talent)

            pity_note = ""
            if result["pity_guarantee"]:
                pity_note = "\n\n🛡️ *Fusion pity guarantee triggered.*"
            elif result["pity_bonus"]:
                pity_note = "\n\n✨ *Fusion pity bonus applied (+20% chance).*"

            if result_talent:
                cosmic_fanfare = ""
                if result_talent.rarity == "Cosmic":
                    cosmic_fanfare = "\n\n🌌✨ **COSMIC TALENT AWAKENED!** The heavens tremble. ✨🌌"

                embed = _talent_embed(
                    ctx, result_talent,
                    title="⚗️ Fusion Successful!",
                    extra_desc=(
                        f"**{talent_a.name}** + **{talent_b.name}** → **{result_talent.name}**"
                        f"{pity_note}{cosmic_fanfare}"
                    ),
                )
            else:
                embed = build_embed(
                    ctx, title="⚗️ Fusion Successful — No Result?",
                    description="Something went wrong resolving the result talent.",
                    color=discord.Color.orange(),
                )

        else:
            failure      = result["failure_outcome"]
            failure_desc = result["failure_description"]

            if failure == "corruption" and result["result_talent"]:
                player.inventory.append(result["result_talent"])
                rt = result["result_talent"]
                exclusive_note = (
                    "\n\n☠️ *A corruption exclusive root has awakened from the darkness.*"
                    if rt.is_corrupted else ""
                )
                embed = _talent_embed(
                    ctx, rt,
                    title="💀 Fusion Failed — Corruption Awakens",
                    extra_desc=(
                        f"*{failure_desc}*{exclusive_note}"
                        f"\n\nFusion pity counter: **{result['new_pity']}**"
                    ),
                )
            elif failure == "mutation" and result["result_talent"]:
                player.inventory.append(result["result_talent"])
                embed = _talent_embed(
                    ctx, result["result_talent"],
                    title="🧬 Fusion Failed — Rare Mutation!",
                    extra_desc=(
                        f"*{failure_desc}*"
                        f"\n\n🧬 *A mutation-exclusive root has emerged — this cannot be spun.*"
                        f"\n\nFusion pity counter: **{result['new_pity']}**"
                    ),
                )
            else:
                title_map = {
                    "backfire":     "💥 Fusion Failed — Backfire",
                    "catastrophic": "☠️ Fusion Failed — Catastrophic",
                }
                embed = build_embed(
                    ctx,
                    title=title_map.get(failure, "❌ Fusion Failed"),
                    description=f"*{failure_desc}*\n\nFusion pity counter: **{result['new_pity']}**",
                    color=discord.Color.red(),
                    show_footer=True,
                )

        await db.save_player_talent_data(player)
        await db.log_fusion(
            discord_id=ctx.author.id,
            guild_id=guild_id,
            talent_a=talent_a.name,
            talent_b=talent_b.name,
            mode=result["resolved_mode"],
            success=result["success"],
            result_name=(result["result_talent"].name if result.get("result_talent") else None),
            failure_outcome=result.get("failure_outcome"),
        )

        await ctx.send(embed=embed)

    # ── /evolve ───────────────────────────────────────────────────────────

    @commands.hybrid_command(name="evolve", description="Evolve a talent in your inventory")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def evolve(self, ctx: commands.Context, slot: int, crystals: int = 0) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(ctx.author.id, guild_id)
        inv      = player.inventory

        if not (1 <= slot <= len(inv)):
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Slot",
                                  description=f"You have **{len(inv)}** talents in inventory."),
                ephemeral=True,
            )
            return

        talent = inv[slot - 1]
        success, updated_talent, message = evolve_talent(player, talent, evolution_items=crystals)
        inv[slot - 1] = updated_talent

        await db.save_player_talent_data(player)

        if success:
            await ctx.send(
                embed=_talent_embed(ctx, updated_talent, title="✦ Evolution", extra_desc=message)
            )
        else:
            await ctx.send(
                embed=error_embed(ctx, title="Evolution Failed", description=message),
                ephemeral=True,
            )

    # ── /lock ─────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="lock", description="Toggle lock on a talent in your inventory")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def lock(self, ctx: commands.Context, slot: int) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction, ephemeral=True)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(ctx.author.id, guild_id)
        inv      = player.inventory

        if not (1 <= slot <= len(inv)):
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Slot",
                                  description=f"You have **{len(inv)}** talents in inventory."),
                ephemeral=True,
            )
            return

        talent        = inv[slot - 1]
        message       = toggle_lock(talent)
        inv[slot - 1] = talent

        await db.save_player_talent_data(player)

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🔒 Lock Toggled",
                description=message,
                color=discord.Color.greyple(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    # ── /set_active ───────────────────────────────────────────────────────

    @commands.hybrid_command(name="set_active", description="Swap a talent from your inventory into your active slot")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def set_active(self, ctx: commands.Context, slot: int) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction, ephemeral=True)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        player   = await _load_player(ctx.author.id, guild_id)
        inv      = player.inventory

        if not (1 <= slot <= len(inv)):
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Slot",
                                  description=f"You have **{len(inv)}** talents in inventory."),
                ephemeral=True,
            )
            return

        chosen = inv[slot - 1]
        player.inventory = [t for t in player.inventory if t is not chosen]
        msg = accept_talent(player, chosen, replace_active=True)

        await db.save_player_talent_data(player)

        await ctx.send(
            embed=_talent_embed(ctx, chosen, title="✅ Active Talent Updated", extra_desc=msg),
            ephemeral=True,
        )

    # ── /tokens ───────────────────────────────────────────────────────────

    @commands.hybrid_command(name="tokens", description="Check how many spin tokens you have")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def tokens(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction, ephemeral=True)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        count    = await db.get_spin_tokens(ctx.author.id, guild_id)

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🎟️ Spin Tokens",
                description=(
                    f"You have **{count}** spin token(s).\n\n"
                    f"Use `/use_spin` to roll, or `/gift_spin @user` to share tokens with another cultivator."
                ),
                color=discord.Color.gold(),
                show_footer=True,
            ),
            ephemeral=True,
        )

    # ── /gift_spin ────────────────────────────────────────────────────────

    @commands.hybrid_command(name="gift_spin", description="Gift spin token(s) from your balance to another cultivator")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def gift_spin(self, ctx: commands.Context, member: discord.Member, amount: int = 1) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction, ephemeral=True)

        if member.id == ctx.author.id:
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Target",
                                  description="You cannot gift tokens to yourself."),
                ephemeral=True,
            )
            return

        if member.bot:
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Target",
                                  description="Bots don't walk the Path."),
                ephemeral=True,
            )
            return

        if not (1 <= amount <= GIFT_MAX):
            await ctx.send(
                embed=error_embed(ctx, title="Invalid Amount",
                                  description=f"You can gift between **1** and **{GIFT_MAX}** token(s) at once."),
                ephemeral=True,
            )
            return

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0

        try:
            from db import cultivators as cult_db
            target_row = await cult_db.get_cultivator(member.id)
        except Exception:
            log.exception("Talent » gift_spin: DB lookup failed for target=%s", member.id)
            await ctx.send(
                embed=error_embed(ctx, title="Database Error",
                                  description="Could not verify target cultivator. Try again later."),
                ephemeral=True,
            )
            return

        if target_row is None:
            await ctx.send(
                embed=error_embed(ctx, title="Not a Cultivator",
                                  description=f"{member.mention} hasn't started their Path yet."),
                ephemeral=True,
            )
            return

        sender_tokens = await db.get_spin_tokens(ctx.author.id, guild_id)
        if sender_tokens < amount:
            await ctx.send(
                embed=error_embed(
                    ctx, title="Not Enough Tokens",
                    description=(
                        f"You only have **{sender_tokens}** token(s). "
                        f"You need **{amount}** to complete this gift."
                    ),
                ),
                ephemeral=True,
            )
            return

        await db.consume_spin_token(ctx.author.id, guild_id, count=amount)
        await db.add_spin_tokens(member.id, guild_id, amount)

        log.info(
            "Talent » gift_spin  sender=%s  recipient=%s  amount=%d",
            ctx.author.id, member.id, amount,
        )

        await ctx.send(
            embed=build_embed(
                ctx,
                title="🎁 Spin Token(s) Gifted",
                description=(
                    f"**{ctx.author.display_name}** sent **{amount}** spin token(s) "
                    f"to {member.mention}!\n\n"
                    f"🎟️ Your remaining tokens: **{sender_tokens - amount}**"
                ),
                color=discord.Color.green(),
                show_footer=True,
            ),
            ephemeral=False,
        )

    # ── /use_spin ─────────────────────────────────────────────────────────

    @commands.hybrid_command(name="use_spin", description="Open a spin session — use as many tokens as you want in one go")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def use_spin(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await safe_defer(ctx.interaction)

        row = await _guard_cultivator(ctx)
        if row is None:
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        tokens   = await db.get_spin_tokens(ctx.author.id, guild_id)

        if tokens < 1:
            await ctx.send(
                embed=error_embed(
                    ctx, title="No Spin Tokens",
                    description=(
                        "You have no spin tokens.\n\n"
                        "Ask a mod to grant you one, or another cultivator can `/gift_spin` you theirs."
                    ),
                ),
                ephemeral=True,
            )
            return

        player = await _load_player(ctx.author.id, guild_id)

        if len(player.inventory) >= INVENTORY_MAX:
            await ctx.send(
                embed=error_embed(
                    ctx, title="Inventory Full",
                    description=(
                        f"Your inventory is full ({INVENTORY_MAX} slots). "
                        f"Fuse or discard talents to make room before spinning."
                    ),
                ),
                ephemeral=True,
            )
            return

        await _do_spin(ctx, player, guild_id, tokens, message=None)


# ---------------------------------------------------------------------------
# Multi-spin engine
# ---------------------------------------------------------------------------

async def _do_spin(
    ctx: commands.Context,
    player: PlayerTalentData,
    guild_id: int,
    tokens: int,
    message: Optional[discord.Message],
) -> None:
    claimed                = await db.get_claimed_one_per_server(guild_id)
    talent, pity_triggered = spin_talent(player, claimed)

    await db.consume_spin_token(ctx.author.id, guild_id)
    tokens_left = tokens - 1

    await db.save_player_talent_data(player)
    await db.log_spin(
        ctx.author.id, guild_id,
        talent.name,
        talent.rarity,
        pity_triggered,
        accepted=False,
    )

    pity_note  = "\n\n✨ *Pity threshold reached — guaranteed pull!*" if pity_triggered else ""
    inv_line   = f"`{len(player.inventory)} / {INVENTORY_MAX}` inventory slots used"
    token_line = f"🎟️ **Tokens remaining:** {tokens_left}"

    extra = f"Accept or discard?{pity_note}\n\n{token_line}\n{inv_line}"
    embed = _talent_embed(ctx, talent, title="🎲 Talent Revealed", extra_desc=extra)
    view  = _SpinSessionView(ctx, player, talent, guild_id, tokens_left)

    if message is None:
        sent = await ctx.send(embed=embed, view=view)
        # Store the message reference so on_timeout can grey out buttons
        if isinstance(sent, discord.Message):
            view.message = sent
    else:
        await message.edit(embed=embed, view=view)
        view.message = message


# ---------------------------------------------------------------------------
# Multi-spin session view
# ---------------------------------------------------------------------------

class _SpinSessionView(discord.ui.View):
    def __init__(
        self,
        ctx: commands.Context,
        player: PlayerTalentData,
        talent: PlayerTalent,
        guild_id: int,
        tokens_left: int,
    ) -> None:
        super().__init__(timeout=90)
        self.ctx         = ctx
        self.player      = player
        self.talent      = talent
        self.guild_id    = guild_id
        self.tokens_left = tokens_left
        self.message: Optional[discord.Message] = None  # set by _do_spin after send

        can_spin_again = tokens_left > 0 and len(player.inventory) < INVENTORY_MAX
        self.accept_spin.disabled  = not can_spin_again
        self.discard_spin.disabled = not can_spin_again

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "This spin session belongs to someone else.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        """Grey out all buttons when the 90-second window expires."""
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True  # type: ignore[union-attr]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass  # message deleted or bot lost permission — safe to ignore

    def _disable_all(self) -> None:
        """Disable every button immediately to block double-clicks."""
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True  # type: ignore[union-attr]

    # ── shared accept helper ──────────────────────────────────────────────

    async def _do_accept(self, interaction: discord.Interaction) -> str:
        replace = self.player.active_talent is None
        msg     = accept_talent(self.player, self.talent, replace_active=replace)
        await db.save_player_talent_data(self.player)
        await db.mark_last_spin_accepted(interaction.user.id, self.guild_id)
        if self.talent.name in ONE_PER_SERVER_TALENTS:
            await db.claim_one_per_server(self.guild_id, interaction.user.id, self.talent.name)
        return msg or "Talent accepted."

    # ── ✅ Accept & Spin Again ────────────────────────────────────────────

    @discord.ui.button(label="✅ Accept & Spin Again", style=discord.ButtonStyle.success, row=0)
    async def accept_spin(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._disable_all()
        self.stop()

        try:
            await self._do_accept(interaction)
        except Exception:
            log.exception("accept_spin: _do_accept failed  user=%s", interaction.user.id)
            await interaction.response.send_message(
                embed=error_embed(interaction, title="Error", description="Something went wrong saving your talent. Please try again."),
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        msg = interaction.message
        if msg is None:
            log.warning("accept_spin: interaction.message is None — cannot edit")
            return

        await _do_spin(self.ctx, self.player, self.guild_id, self.tokens_left, message=msg)

    # ── ✅ Accept & Stop ──────────────────────────────────────────────────

    @discord.ui.button(label="✅ Accept & Stop", style=discord.ButtonStyle.primary, row=0)
    async def accept_stop(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._disable_all()
        self.stop()

        try:
            msg = await self._do_accept(interaction)
        except Exception:
            log.exception("accept_stop: _do_accept failed  user=%s", interaction.user.id)
            await interaction.response.send_message(
                embed=error_embed(interaction, title="Error", description="Something went wrong saving your talent. Please try again."),
                ephemeral=True,
            )
            return

        await safe_edit(
            interaction,
            embed=build_embed(
                interaction,  # type: ignore[arg-type]
                title="✅ Talent Accepted",
                description=f"{msg}\n\n🎟️ Tokens remaining: **{self.tokens_left}**",
                color=discord.Color.green(),
            ),
            view=None,
        )

    # ── ❌ Discard & Spin Again ───────────────────────────────────────────

    @discord.ui.button(label="❌ Discard & Spin Again", style=discord.ButtonStyle.danger, row=1)
    async def discard_spin(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._disable_all()
        self.stop()

        reject_talent(self.talent)
        await interaction.response.defer()

        msg = interaction.message
        if msg is None:
            log.warning("discard_spin: interaction.message is None — cannot edit")
            return

        await _do_spin(self.ctx, self.player, self.guild_id, self.tokens_left, message=msg)

    # ── ❌ Discard & Stop ─────────────────────────────────────────────────

    @discord.ui.button(label="❌ Discard & Stop", style=discord.ButtonStyle.secondary, row=1)
    async def discard_stop(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._disable_all()
        self.stop()

        discard_msg = reject_talent(self.talent)
        if not isinstance(discard_msg, str) or not discard_msg:
            discard_msg = "Talent discarded."

        await safe_edit(
            interaction,
            embed=build_embed(
                interaction,  # type: ignore[arg-type]
                title="❌ Talent Discarded",
                description=f"{discard_msg}\n\n🎟️ Tokens remaining: **{self.tokens_left}**",
                color=discord.Color.red(),
            ),
            view=None,
        )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Talent(bot))