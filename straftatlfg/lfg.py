import re
import random
import discord
from redbot.core import commands
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
        self.active_sticky_channels = [] # List of channel IDs
        self.sticky_cache = {} # channel_id: message_id

    async def _handle_sticky(self, channel: discord.TextChannel):
        # Delete old message if it exists
        old_msg_id = self.sticky_cache.get(channel.id)
        if old_msg_id:
            try:
                old_msg = await channel.fetch_message(old_msg_id)
                await old_msg.delete()
            except Exception:
                pass

        embed = discord.Embed(
            title="How to use the LFG system",
            description=(
                "**Role Toggle**: You can use `!lfg-role` in <#1310689512615051345> to add/remove the role at any time!\n\n"
                "To post an LFG message, use the following command: `!lfg <lobby_id> <notes>`\n"
                "> **Lobby ID**: Must be numerical, no alphabetical characters.\n"
                "> **Notes**: Describe what you are looking for like casual or competitive matches!\n"
            ),
            color=discord.Color.blue()
        )
        
        try:
            new_msg = await channel.send(embed=embed)
            self.sticky_cache[channel.id] = new_msg.id
        except Exception:
            if channel.id in self.active_sticky_channels:
                self.active_sticky_channels.remove(channel.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        
        if message.channel.id not in self.active_sticky_channels:
            return

        # Don't trigger on our own sticky message
        if message.id == self.sticky_cache.get(message.channel.id):
            return
            
        await self._handle_sticky(message.channel)

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

        channel_id = ctx.channel.id
        
        if channel_id in self.active_sticky_channels:
            # Disable sticky
            self.active_sticky_channels.remove(channel_id)
            
            # Cleanup last message
            old_msg_id = self.sticky_cache.pop(channel_id, None)
            if old_msg_id:
                try:
                    old_msg = await ctx.channel.fetch_message(old_msg_id)
                    await old_msg.delete()
                except Exception:
                    pass
            
            await ctx.send("Sticky message disabled in this channel.", delete_after=10)
        else:
            # Enable sticky
            self.active_sticky_channels.append(channel_id)
            await self._handle_sticky(ctx.channel)
            await ctx.send(f"Sticky message enabled in {ctx.channel.mention}.", delete_after=10)

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
