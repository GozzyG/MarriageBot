from discord.ext import commands

from cogs import utils


class Marriage(utils.Cog):
    """The marriage cog, handling all marriage/divorce/etc commands"""

    @commands.command(aliases=['marry'])
    @commands.cooldown(1, 5, commands.BucketType.user)
    @utils.checks.bot_is_ready()
    async def propose(self, ctx:utils.Context, *, target:utils.converters.UnblockedMember):
        """Lets you propose to another Discord user"""

        # Variables we're gonna need for later
        instigator = ctx.author
        instigator_tree = utils.FamilyTreeMember.get(instigator.id, ctx.family_guild_id)
        target_tree = utils.FamilyTreeMember.get(target.id, ctx.family_guild_id)

        # Manage output strings
        text_processor = utils.random_text.RandomText('propose', instigator, target)
        text = text_processor.process()
        if text:
            return await ctx.send(text)

        # See if our user is already married
        if instigator_tree._partner:
            return await ctx.send(text_processor.instigator_is_unqualified())

        # See if the *target* is already married
        if target_tree._partner:
            return await ctx.send(text_processor.target_is_unqualified())

        # See if they're already related
        async with ctx.channel.typing():
            relation = instigator_tree.get_relation(target_tree)
        if relation and not self.bot.allows_incest(ctx.guild.id):
            return await ctx.send(text_processor.target_is_family())

        # Check the size of their trees
        max_family_members = self.bot.guild_settings[ctx.guild.id]['max_family_members']
        async with ctx.channel.typing():
            if instigator_tree.family_member_count + target_tree.family_member_count > max_family_members:
                return await ctx.send(f"If you added {target.mention} to your family, you'd have over {max_family_members} in your family, so I can't allow you to do that. Sorry!")

        # Neither are married, set up the proposal
        await ctx.send(text_processor.valid_target())
        await self.bot.proposal_cache.add(instigator, target, 'MARRIAGE')

        # Wait for a response
        check = utils.AcceptanceCheck(target.id, ctx.channel.id)
        try:
            await check.wait_for_response(self.bot)
        except utils.AcceptanceCheck.TIMEOUT:
            return await ctx.send(text_processor.request_timeout(), ignore_error=True)

        # They said no
        if check.response == 'NO':
            await self.bot.proposal_cache.remove(instigator, target)
            return await ctx.send(text_processor.request_denied(), ignore_error=True)

        # They said yes!
        async with self.bot.database() as db:
            await db.marry(instigator, target, ctx.family_guild_id)
        await ctx.send(text_processor.request_accepted(), ignore_error=True)

        # Cache values locally
        instigator_tree._partner = target.id
        target_tree._partner = instigator.id

        # Ping em off over redis
        async with self.bot.redis() as re:
            await re.publish_json('TreeMemberUpdate', instigator_tree.to_json())
            await re.publish_json('TreeMemberUpdate', target_tree.to_json())

        # Remove users from proposal cache
        await self.bot.proposal_cache.remove(instigator, target)

    @commands.command()
    @commands.cooldown(1, 5, commands.BucketType.user)
    @utils.checks.bot_is_ready()
    async def divorce(self, ctx:utils.Context):
        """Divorces you from your current spouse"""

        # Variables we're gonna need for later
        instigator = ctx.author
        instigator_tree = utils.FamilyTreeMember.get(instigator.id, ctx.family_guild_id)

        # Manage output strings
        text_processor = utils.random_text.RandomText('divorce', instigator, self.bot.get_user(instigator_tree._partner))

        # See if they have a partner to divorce
        if instigator_tree._partner == None:
            await ctx.send(text_processor.instigator_is_unqualified())
            return

        # They have a partner - fetch their data
        target_tree = instigator_tree.partner

        # Remove them from the database
        async with self.bot.database() as db:
            await db(
                'DELETE FROM marriages WHERE (user_id=$1 OR user_id=$2) AND guild_id=$3',
                instigator.id,
                target_tree.id,
                ctx.family_guild_id
            )
        await ctx.send(text_processor.valid_target())

        # Remove from cache
        instigator_tree._partner = None
        target_tree._partner = None

        # Ping over redis
        async with self.bot.redis() as re:
            await re.publish_json('TreeMemberUpdate', instigator_tree.to_json())
            await re.publish_json('TreeMemberUpdate', target_tree.to_json())


def setup(bot:utils.CustomBot):
    x = Marriage(bot)
    bot.add_cog(x)
