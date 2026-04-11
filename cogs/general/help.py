from __future__ import annotations

import discord
from discord.ext import commands

from ui.embed import build_embed

_HIDDEN_COGS: frozenset[str] = frozenset({"General"})

# Map subfolder name → display label + emoji
_CATEGORY_META: dict[str, tuple[str, str]] = {
    "general":     ("⚙️ General",      "Core bot commands"),
    "cultivation": ("🌿 Cultivation",  "Qi, meditation & breakthroughs"),
    "pvp":         ("⚔️ PvP",           "Combat, duels & ambushes"),
    "talent":      ("🌟 Talent",        "Talents, spins & fusion"),
    "admin":       ("🛠️ Admin",         "Staff-only commands"),
}
_FALLBACK_CATEGORY = ("📦 Other", "Miscellaneous commands")


def _category_for_cog(cog: commands.Cog) -> str:
    """
    Derive a category key from the cog's module path.
    e.g. 'cogs.cultivation.cultivate' → 'cultivation'
         'cogs.general.help'          → 'general'
    """
    module = getattr(cog, "__module__", "") or ""
    parts  = module.split(".")
    # parts[0] == 'cogs', parts[1] == subfolder, parts[2] == filename
    if len(parts) >= 3 and parts[0] == "cogs":
        return parts[1]
    if len(parts) >= 2 and parts[0] == "cogs":
        return parts[1]
    return "__other__"


class HelpSelect(discord.ui.Select):
    def __init__(self, pages: dict[str, discord.Embed]) -> None:
        self.pages = pages
        options = [
            discord.SelectOption(
                label=label,
                value=key,
                description=desc[:100],
            )
            for key, (label, desc) in pages["__meta__"].items()
        ]
        super().__init__(
            placeholder="Choose a category…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        key   = self.values[0]
        embed = self.pages.get(key)
        if embed:
            await interaction.response.edit_message(embed=embed, view=self.view)
        else:
            await interaction.response.defer()


class HelpView(discord.ui.View):
    def __init__(self, pages: dict[str, discord.Embed], author_id: int) -> None:
        super().__init__(timeout=120)
        self.author_id = author_id
        self.add_item(HelpSelect(pages))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This menu belongs to someone else.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]


class General(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="help", description="Browse all commands by category")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def help(self, ctx: commands.Context) -> None:
        # ── 1. Bucket commands by subfolder category ──────────────────
        buckets: dict[str, list[str]] = {}

        for cog_name, cog in sorted(self.bot.cogs.items()):
            if cog_name in _HIDDEN_COGS:
                continue

            cat_key = _category_for_cog(cog)

            for cmd in cog.get_commands():
                if cmd.hidden:
                    continue
                try:
                    if cmd.checks and not all(check(ctx) for check in cmd.checks):
                        continue
                except Exception:
                    continue

                sig = f"`/{cmd.name}"
                if cmd.signature:
                    sig += f" {cmd.signature}"
                sig += f"` — {cmd.description or cmd.brief or 'No description'}"

                buckets.setdefault(cat_key, []).append(sig)

        if not buckets:
            await ctx.send(
                embed=build_embed(ctx, title="Help", description="No commands available.",
                                  color=discord.Color.blurple()),
                ephemeral=True,
            )
            return

        # ── 2. Build one embed per category + overview embed ──────────
        # pages["__meta__"] holds {key: (label, desc)} for the select menu
        pages: dict[str, discord.Embed | dict] = {"__meta__": {}}

        overview_lines: list[str] = []

        for cat_key, cmd_lines in sorted(buckets.items()):
            label, desc = _CATEGORY_META.get(cat_key, _FALLBACK_CATEGORY)

            # record meta for select options
            pages["__meta__"][cat_key] = (label, f"{len(cmd_lines)} command(s)")  # type: ignore[index]

            embed = build_embed(
                ctx,
                title=f"{label} Commands",
                description=(
                    f"*{desc}*\n\n"
                    + "\n".join(cmd_lines)
                ),
                color=discord.Color.blurple(),
            )
            pages[cat_key] = embed

            overview_lines.append(f"**{label}** — {len(cmd_lines)} command(s)")

        overview_embed = build_embed(
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

        # ── 3. If only one category exists, skip the dropdown ─────────
        non_meta = {k: v for k, v in pages.items() if k != "__meta__"}
        if len(non_meta) == 1:
            await ctx.send(embed=next(iter(non_meta.values())), ephemeral=True)  # type: ignore[arg-type]
            return

        view = HelpView(pages, ctx.author.id)  # type: ignore[arg-type]
        await ctx.send(embed=overview_embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))