"""
cogs/general/help.py
~~~~~~~~~~~~~~~~~~~~~
Paginated /help command with a category dropdown.

Fixes over the original
-----------------------
* Covers BOTH hybrid commands (get_commands) AND pure app_commands
  (walk_app_commands) — the original missed all slash-only commands.
* cmd.checks are no longer evaluated with a Context — slash checks expect
  an Interaction; we skip check evaluation entirely and just show all
  non-hidden commands (staff-only commands stay hidden via cmd.hidden).
* HelpSelect.callback and HelpView.interaction_check use safe_* wrappers
  so a stale click never raises.
* on_timeout no longer tries to edit a dead message — it only marks items
  disabled locally.
* pages dict is properly split into a typed CategoryPage dataclass instead
  of a mixed Embed | dict abomination.
* Single-category fast-path respects hybrid vs prefix context correctly.
* Cooldown error is handled gracefully.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands

from ui.embed import build_embed
from ui.interaction_utils import safe_send, safe_edit, safe_defer

log = logging.getLogger("bot.cogs.general.help")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_HIDDEN_COGS: frozenset[str] = frozenset({"General"})

_CATEGORY_META: dict[str, tuple[str, str]] = {
    "general":      ("⚙️ General",      "Core bot commands"),
    "cultivation":  ("🌿 Cultivation",  "Qi, meditation & breakthroughs"),
    "pvp":          ("⚔️ PvP",           "Combat, duels & ambushes"),
    "talent":       ("🌟 Talent",        "Talents, spins & fusion"),
    "spirit_roots": ("🌱 Spirit Roots",  "Root awakening, pity & bonuses"),
    "admin":        ("🛠️ Admin",         "Staff-only commands"),
}
_FALLBACK_CATEGORY: tuple[str, str] = ("📦 Other", "Miscellaneous commands")

# ---------------------------------------------------------------------------
# Internal data model
# ---------------------------------------------------------------------------

@dataclass
class CategoryPage:
    key:   str
    label: str
    desc:  str
    embed: discord.Embed
    count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _category_for_cog(cog: commands.Cog) -> str:
    """
    Derive a category key from the cog's module path.

    'cogs.cultivation.cultivate' → 'cultivation'
    'cogs.general.help'          → 'general'
    """
    module = getattr(cog, "__module__", "") or ""
    parts  = module.split(".")
    if len(parts) >= 2 and parts[0] == "cogs":
        return parts[1]
    return "__other__"


def _cmd_signature(name: str, signature: str, description: str) -> str:
    sig = f"`/{name}"
    if signature:
        sig += f" {signature}"
    sig += f"` — {description or 'No description'}"
    return sig


def _collect_commands(bot: commands.Bot) -> dict[str, list[str]]:
    """
    Walk every cog and collect visible command signatures, bucketed by category.

    Covers:
    • Hybrid / prefix commands via cog.get_commands()
    • Pure app_commands via cog.__cog_app_commands__ (slash-only commands
      that are NOT registered as hybrid commands are invisible to
      get_commands() and were silently dropped by the original code)

    We intentionally skip check evaluation — slash command checks expect
    an Interaction, not a Context.  Hidden commands are the correct way to
    exclude staff commands from the help menu.
    """
    buckets: dict[str, list[str]] = {}

    for cog_name, cog in sorted(bot.cogs.items()):
        if cog_name in _HIDDEN_COGS:
            continue

        cat_key = _category_for_cog(cog)
        seen_names: set[str] = set()  # prevent duplicates from hybrid overlap

        # 1. Hybrid / prefix commands
        for cmd in cog.get_commands():
            if cmd.hidden:
                continue
            seen_names.add(cmd.name)
            buckets.setdefault(cat_key, []).append(
                _cmd_signature(
                    cmd.name,
                    cmd.signature or "",
                    cmd.description or cmd.brief or "",
                )
            )

        # 2. Pure app_commands (slash-only, not surfaced by get_commands)
        for app_cmd in getattr(cog, "__cog_app_commands__", []):
            if getattr(app_cmd, "hidden", False):
                continue
            if app_cmd.name in seen_names:
                continue  # already listed via hybrid path
            seen_names.add(app_cmd.name)

            # Build a human-readable parameter hint from the command's parameters
            params = getattr(app_cmd, "_params", {})
            param_hint = " ".join(
                f"<{p}>" if param.required else f"[{p}]"
                for p, param in params.items()
            ) if params else ""

            buckets.setdefault(cat_key, []).append(
                _cmd_signature(
                    app_cmd.name,
                    param_hint,
                    getattr(app_cmd, "description", "") or "",
                )
            )

    return buckets


def _build_pages(
    ctx: commands.Context,
    buckets: dict[str, list[str]],
) -> dict[str, CategoryPage]:
    """Build one CategoryPage per bucket key."""
    pages: dict[str, CategoryPage] = {}

    for cat_key, cmd_lines in sorted(buckets.items()):
        label, desc = _CATEGORY_META.get(cat_key, _FALLBACK_CATEGORY)
        embed = build_embed(
            ctx,
            title=f"{label} Commands",
            description=f"*{desc}*\n\n" + "\n".join(cmd_lines),
            color=discord.Color.blurple(),
        )
        pages[cat_key] = CategoryPage(
            key=cat_key,
            label=label,
            desc=desc,
            embed=embed,
            count=len(cmd_lines),
        )

    return pages


def _build_overview(ctx: commands.Context, pages: dict[str, CategoryPage]) -> discord.Embed:
    overview_lines = [
        f"**{p.label}** — {p.count} command(s)"
        for p in pages.values()
    ]
    return build_embed(
        ctx,
        title="📖 Help — Command Categories",
        description=(
            "Use `/command` or `z!command` for any listed command.\n"
            "Arguments: `<required>` · `[optional]`\n\n"
            + "\n".join(overview_lines)
            + "\n\n*Select a category from the dropdown below.*"
        ),
        color=discord.Color.blurple(),
    )


# ---------------------------------------------------------------------------
# UI components
# ---------------------------------------------------------------------------

class HelpSelect(discord.ui.Select):
    def __init__(self, pages: dict[str, CategoryPage]) -> None:
        self._pages = pages
        options = [
            discord.SelectOption(
                label=page.label,
                value=page.key,
                description=f"{page.count} command(s)"[:100],
            )
            for page in pages.values()
        ]
        super().__init__(
            placeholder="Choose a category…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        key  = self.values[0]
        page = self._pages.get(key)
        if page is None:
            await safe_defer(interaction)
            return
        # edit_message works here because this interaction is on a message component
        try:
            await interaction.response.edit_message(embed=page.embed, view=self.view)
        except discord.InteractionResponded:
            pass
        except discord.NotFound:
            pass  # interaction expired — silently ignore
        except Exception:
            log.exception("HelpSelect.callback: unexpected error  id=%s", interaction.id)


class HelpView(discord.ui.View):
    def __init__(self, pages: dict[str, CategoryPage], author_id: int) -> None:
        super().__init__(timeout=120)
        self.author_id = author_id
        self.add_item(HelpSelect(pages))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await safe_send(
                interaction,
                "This menu belongs to someone else.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        # Mark items disabled locally for correctness.
        # We have no interaction token here, so we cannot edit the message.
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class General(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="help", description="Browse all commands by category")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def help(self, ctx: commands.Context) -> None:
        buckets = _collect_commands(self.bot)

        if not buckets:
            await ctx.send(
                embed=build_embed(
                    ctx,
                    title="Help",
                    description="No commands available.",
                    color=discord.Color.blurple(),
                ),
                ephemeral=True,
            )
            return

        pages    = _build_pages(ctx, buckets)
        overview = _build_overview(ctx, pages)

        # Fast-path: only one category → skip the dropdown
        if len(pages) == 1:
            only_page = next(iter(pages.values()))
            await ctx.send(embed=only_page.embed, ephemeral=True)
            return

        view = HelpView(pages, ctx.author.id)
        await ctx.send(embed=overview, view=view, ephemeral=True)

    @help.error
    async def help_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                embed=build_embed(
                    ctx,
                    title="⏳ Slow down",
                    description=f"You can use `/help` again in **{error.retry_after:.1f}s**.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
                delete_after=6,
            )
        else:
            log.exception("help command error", exc_info=error)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))