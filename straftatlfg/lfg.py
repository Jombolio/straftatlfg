import re
import random
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

class LFG(commands.Cog):
    """
    LFG command with role pings and cooldowns
    """

    LFG_ROLE_ID = 1358388775570637001
    LFG_CHANNEL_ID = 1284536580941287598
    TEST_ROLE_ID = 1270478869610238084
    TEST_CHANNEL_ID = 1269794533923754089

    def __init__(self, bot: Red):
        self.bot = bot
        self.sticky_enabled = False
        self.sticky_channel_id = None
        self.last_sticky_id = None

    async def _send_sticky(self, channel: discord.TextChannel):
        if self.last_sticky_id:
            try:
                msg = await channel.fetch_message(self.last_sticky_id)
                await msg.delete()
            except Exception:
                pass

        embed = discord.Embed(
            title="How to use the LFG system",
            description=(
                "To post an LFG message, use the following command:\n"
                "`!lfg <lobby_id> <notes>`\n\n"
                "• **Lobby ID**: Must be numerical.\n"
                "• **Notes**: Describe what you are looking for.\n"
                "• **Toggle Role**: Use `!lfg-role` to subscribe/unsubscribe from pings."
            ),
            color=discord.Color.blue()
        )
        new_msg = await channel.send(embed=embed)
        self.last_sticky_id = new_msg.id

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        
        if not self.sticky_enabled or message.channel.id != self.sticky_channel_id:
            return

        if message.id == self.last_sticky_id:
            return
            
        await self._send_sticky(message.channel)

    async def _process_lfg(self, ctx: commands.Context, channel_id: int, lobby_id: str, notes: str):
        if ctx.channel.id != channel_id:
            return await ctx.send(f"This command can only be used in <#{channel_id}>.", delete_after=10)

        if not lobby_id.isdigit():
            ctx.command.reset_cooldown(ctx)
            return await ctx.send("The Lobby ID must contain only numerical characters.", delete_after=10)

        # Sanitize notes: remove masked links and raw URLs
        notes = re.sub(r"\[([^\]]+)\]\(https?://[^\s\)]+\)", r"\1", notes)
        notes = re.sub(r"https?://[^\s]+", "", notes)

        role = ctx.guild.get_role(self.LFG_ROLE_ID)
        if not role:
            return await ctx.send("LFG Role not found. Please contact an administrator.")

        title = "Euuuuuugh!" if random.randint(1, 1000) == 1 else "Looking For Group"
        color = discord.Color.green() if any(r.id == 1387554310832918528 for r in ctx.author.roles) else discord.Color.blue()
        
        embed = discord.Embed(
            title=title,
            color=color,
            description=notes
        )
        embed.add_field(name="Lobby ID", value=f"`{lobby_id}`", inline=True)
        embed.add_field(name="Host", value=ctx.author.mention, inline=True)
        embed.set_footer(text="Join the lobby using the ID above!", icon_url=ctx.author.display_avatar.url)

        content = f"{role.mention}"
        
        await ctx.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=[role])
        )

        try:
            await ctx.message.add_reaction("✅")
        except discord.DiscordException:
            pass

    @commands.command()
    @commands.guild_only()
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def lfg(self, ctx: commands.Context, lobby_id: str, *, notes: str):
        """
        Post an LFG message.
        
        Syntax: [p]lfg <lobby_id> <notes>
        Lobby ID must be numerical.
        """
        await self._process_lfg(ctx, self.LFG_CHANNEL_ID, lobby_id, notes)

    @commands.command()
    @commands.guild_only()
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def testlfg(self, ctx: commands.Context, lobby_id: str, *, notes: str):
        """
        Test LFG command. 
        Only usable by specific test role.
        """
        if not any(role.id == self.TEST_ROLE_ID for role in ctx.author.roles):
            return await ctx.send("You do not have permission to use this test command.", delete_after=10)
        
        await self._process_lfg(ctx, self.TEST_CHANNEL_ID, lobby_id, notes)

    @commands.command(name="lfg-role")
    @commands.guild_only()
    async def lfg_role(self, ctx: commands.Context):
        """
        Toggle the LFG role.
        """
        role = ctx.guild.get_role(self.LFG_ROLE_ID)
        if not role:
            return await ctx.send("LFG Role not found. Please contact an administrator.")

        if role in ctx.author.roles:
            try:
                await ctx.author.remove_roles(role, reason="LFG role toggle")
                await ctx.send("Removed the LFG role.", delete_after=10)
                await ctx.message.add_reaction("❌")
            except discord.Forbidden:
                await ctx.send("I do not have permission to remove that role.")
        else:
            try:
                await ctx.author.add_roles(role, reason="LFG role toggle")
                await ctx.send("Added the LFG role.", delete_after=10)
                await ctx.message.add_reaction("✅")
            except discord.Forbidden:
                await ctx.send("I do not have permission to add that role.")

    @commands.command(name="toggle-sticky")
    @commands.guild_only()
    async def toggle_sticky(self, ctx: commands.Context):
        """
        Toggle the sticky info message in the current channel.
        """
        if not any(role.id == self.TEST_ROLE_ID for role in ctx.author.roles):
            return await ctx.send("You do not have permission to use this command.", delete_after=10)

        guild_data = self.config.guild(ctx.guild)
        current = await guild_data.sticky_enabled()
        new_state = not current
        
        await guild_data.sticky_enabled.set(new_state)

        if new_state:
            await guild_data.sticky_channel_id.set(ctx.channel.id)
            await self._send_sticky(ctx.channel)
            await ctx.send(f"Sticky message enabled in {ctx.channel.mention}.", delete_after=10)
        else:
            last_id = await guild_data.last_sticky_id()
            sticky_channel_id = await guild_data.sticky_channel_id()
            if last_id and sticky_channel_id:
                try:
                    channel = ctx.guild.get_channel(sticky_channel_id)
                    if channel:
                        msg = await channel.fetch_message(last_id)
                        await msg.delete()
                except (discord.NotFound, discord.Forbidden, AttributeError):
                    pass
            await guild_data.last_sticky_id.set(None)
            await guild_data.sticky_channel_id.set(None)
            await ctx.send("Sticky message disabled.", delete_after=10)

    @lfg.error
    @testlfg.error
    @lfg_role.error
    async def lfg_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"You are on cooldown. Try again in {error.retry_after:.0f} seconds.", delete_after=10)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"Incorrect syntax. Use `{ctx.prefix}lfg <lobby_id> <notes>`", delete_after=10)
        else:
            # Log other errors
            raise error
