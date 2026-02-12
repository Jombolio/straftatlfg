from .lfg import LFG


async def setup(bot):
    cog = LFG(bot)
    await bot.add_cog(cog)
