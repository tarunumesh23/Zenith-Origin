from __future__ import annotations

import discord
from discord.ext import commands

SCENES = [
    {
        "title": "Scene 1 — The Forgotten Scroll",
        "description": (
            "You are an orphan living at the foot of the **Ironveil Sect** mountain.\n\n"
            "While sweeping the outer courtyard, you discover a **torn cultivation scroll** "
            "half-buried in the dirt. An inner disciple is watching from a distance."
        ),
        "choices": [
            {"label": "📜 Study the scroll in secret", "score": 2, "response": "Your eyes trace the ancient characters hungrily. A faint warmth stirs in your chest — Qi, reacting to your intent."},
            {"label": "🙇 Hand it to the inner disciple", "score": 0, "response": "The disciple takes it without a word. He doesn't even glance back. You feel nothing but the cold wind."},
        ]
    },
    {
        "title": "Scene 2 — The Dying Elder",
        "description": (
            "Deep in the mountain forest you stumble upon an **injured Elder** collapsed against a tree. "
            "His robes bear the sect's crest. He whispers he was ambushed — and that he carries a **Qi-gathering jade**."
        ),
        "choices": [
            {"label": "🩹 Help him back to the sect", "score": 2, "response": "You bear his weight for three miles. Before losing consciousness he presses something cold into your palm — the jade."},
            {"label": "👁️ Leave him and take the jade", "score": 0, "response": "Your fingers close around the jade. It feels heavy — not just in weight. His fading eyes say everything."},
        ]
    },
    {
        "title": "Scene 3 — The Trial Gate",
        "description": (
            "The sect holds its **once-a-decade trial**. A massive stone gate stands before you — "
            "only those with an awakened spirit root may pass. You feel a faint pulse in your dantian... "
            "but the gate examiner says *yours is too weak*."
        ),
        "choices": [
            {"label": "🔥 Meditate overnight and try again at dawn", "score": 2, "response": "Hours pass. Pain. Stillness. Then — a crack of light in the stone. The gate shudders and opens an inch."},
            {"label": "💰 Bribe another examiner to falsify results", "score": 0, "response": "The coins exchange hands. You step through. But you feel it — the Dao does not lie to itself."},
        ]
    },
    {
        "title": "Scene 4 — The Rival",
        "description": (
            "A fellow outer disciple — **Shen Yao**, known for his cruel streak — challenges you to a duel. "
            "He fights dirty. Mid-match he uses a forbidden technique that ruptures your Qi flow. "
            "The crowd goes silent."
        ),
        "choices": [
            {"label": "⚖️ Report the forbidden technique to the sect", "score": 2, "response": "It costs you pride. But truth has weight in cultivation — the elders take note of your integrity."},
            {"label": "🗡️ Use the torn scroll's technique in retaliation", "score": 0, "response": "You win. But using an unsanctioned art in public draws the wrong kind of attention."},
        ]
    },
    {
        "title": "Scene 5 — The Heavenly Convergence",
        "description": (
            "Once every hundred years, **Qi floods the mortal realm** for a single night. "
            "Every cultivator scrambles to absorb as much as possible. "
            "You find a quiet peak — and an **elderly mortal woman** sitting there, trying to warm her hands over a dying fire."
        ),
        "choices": [
            {"label": "🌿 Sit beside her and share the Qi convergence", "score": 2, "response": "You circulate the Qi slowly, letting some warmth bleed outward. The old woman smiles. The heavens… take notice."},
            {"label": "⚡ Absorb everything you can alone", "score": 0, "response": "Your cultivation rises sharply. But somewhere deep in your spirit root, a hairline crack forms. The Dao remembers."},
        ]
    },
]

OUTCOMES = {
    "pass": {
        "title": "⚡ The Dao Opens Before You",
        "color": discord.Color.gold(),
        "description": (
            "The heavens have weighed your heart and found it **worthy**.\n\n"
            "Your spirit root awakens fully. A golden light envelops your dantian.\n"
            "You have stepped onto the **Path of Cultivation**.\n\n"
            "*\"One who cultivates the self before the art — such a person the Dao does not reject.\"*"
        )
    },
    "retry": {
        "title": "🌫️ The Path Stirs, But Does Not Open",
        "color": discord.Color.greyple(),
        "description": (
            "Your heart carries **both light and shadow** in equal measure.\n\n"
            "The Dao does not deny you — but it does not embrace you yet.\n"
            "Reflect. Grow. Return when your conviction is unshaken.\n\n"
            "*\"A wavering flame can still become a bonfire. Not today — but perhaps tomorrow.\"*"
        )
    },
    "fail": {
        "title": "🌑 The Dao Turns Its Back",
        "color": discord.Color.red(),
        "description": (
            "Your choices have revealed a heart **hardened by greed and shortcuts**.\n\n"
            "The spirit root recoils. The gate does not open.\n"
            "You remain a mortal — for now, and perhaps forever.\n\n"
            "*\"Power sought without virtue is a blade with no handle — it only cuts the one who holds it.\"*"
        )
    }
}


class SceneView(discord.ui.View):
    def __init__(self, cog: commands.Cog, ctx: commands.Context, scene_index: int, score: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.ctx = ctx
        self.scene_index = scene_index
        self.score = score

        for i, choice in enumerate(SCENES[scene_index]["choices"]):
            btn = discord.ui.Button(
                label=choice["label"],
                style=discord.ButtonStyle.primary,
                custom_id=str(i)
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, choice_index: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message("This isn't your journey.", ephemeral=True)
                return

            choice = SCENES[self.scene_index]["choices"][choice_index]
            new_score = self.score + choice["score"]
            next_index = self.scene_index + 1

            if next_index < len(SCENES):
                next_scene = SCENES[next_index]
                embed = discord.Embed(
                    title=next_scene["title"],
                    description=(
                        f"*{choice['response']}*\n\n"
                        f"――――――――――――――――――――\n\n"
                        f"{next_scene['description']}"
                    ),
                    color=discord.Color.dark_teal()
                )
                embed.set_footer(text=f"Scene {next_index + 1} of {len(SCENES)}")
                next_view = SceneView(self.cog, self.ctx, next_index, new_score)
                await interaction.response.edit_message(embed=embed, view=next_view)
            else:
                await self.cog.send_outcome(self.ctx, new_score, interaction)

        return callback

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True