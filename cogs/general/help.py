from __future__ import annotations

import discord
from discord.ext import commands

from ui.embed import build_embed

# Cogs listed here are excluded from the help output entirely.
_HIDDEN_COGS: frozenset[str] = frozenset({"General"})


class General(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="help", description="Shows all available commands")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def help(self, ctx: commands.Context) -> None:
        fields = self._build_help_fields(ctx)

        embed = build_embed(
            ctx,
            title="Available Commands",
            description=(
                "Use `/command` or `z!command` for any of the below.\n"
                "Arguments shown as `<required>` or `[optional]`."
            ),
            fields=fields or [{"name": "No commands found", "value": "\u200b"}],
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, ephemeral=True)

    def _build_help_fields(self, ctx: commands.Context) -> list[dict]:
        """
        Return one embed field per visible cog, listing its commands.
        Commands the invoker cannot run (failed checks) are omitted.
        """
        fields: list[dict] = []

        for cog_name, cog in sorted(self.bot.cogs.items()):
            if cog_name in _HIDDEN_COGS:
                continue

            visible: list[str] = []
            for cmd in cog.get_commands():
                if cmd.hidden:
                    continue
                # Skip commands the user has no access to
                try:
                    # can_run is sync for most checks; async checks are skipped gracefully
                    if not cmd.checks or all(check(ctx) for check in cmd.checks):
                        signature = f"`/{cmd.name}"
                        if cmd.signature:
                            signature += f" {cmd.signature}"
                        signature += f"` — {cmd.description or cmd.brief or 'No description'}"
                        visible.append(signature)
                except Exception:
                    pass

            if visible:
                fields.append({
                    "name": cog_name,
                    "value": "\n".join(visible),
                    "inline": False,
                })

        return fields


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))