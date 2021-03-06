from datetime import datetime as dt
import typing

import asyncpg
from discord.ext import commands

from cogs import utils


class ModeratorOnly(utils.Cog):

    @commands.command()
    @utils.checks.is_bot_administrator()
    async def uncache(self, ctx:utils.Context, user:utils.converters.UserID):
        """Removes a user from the propsal cache."""

        await self.bot.proposal_cache.remove(user)
        await ctx.send("Sent Redis request to remove user from cache.")

    @commands.command()
    @utils.checks.is_bot_administrator()
    async def recache(self, ctx:utils.Context, user:utils.converters.UserID, guild_id:int=0):
        """Recaches a user's family tree member object"""

        # Read data from DB
        async with self.bot.database() as db:
            parent = await db('SELECT parent_id FROM parents WHERE child_id=$1 AND guild_id=$2', user, guild_id)
            children = await db('SELECT child_id FROM parents WHERE parent_id=$1 AND guild_id=$2', user, guild_id)
            partner = await db('SELECT partner_id FROM marriages WHERE user_id=$1 AND guild_id=$2', user, guild_id)

        # Load data into cache
        children = [i['child_id'] for i in children]
        parent_id = parent[0]['parent_id'] if len(parent) > 0 else None
        partner_id = partner[0]['partner_id'] if len(partner) > 0 else None
        f = utils.FamilyTreeMember(
            user,
            children=children,
            parent_id=parent_id,
            partner_id=partner_id,
            guild_id=guild_id,
        )

        # Push update via redis
        async with self.bot.redis() as re:
            await re.publish_json('TreeMemberUpdate', f.to_json())

        # Output to user
        await ctx.send("Published update.")

    @commands.command()
    @utils.checks.is_bot_administrator()
    async def recachefamily(self, ctx:utils.Context, user:utils.converters.UserID, guild_id:int=0):
        """Recaches a user's family tree member object, but through their whole family"""

        # Get connections
        db = await self.bot.database.get_connection()
        re = await self.bot.redis.get_connection()

        # Loop through their tree
        family = utils.FamilyTreeMember.get(user, guild_id).span(expand_upwards=True, add_parent=True)[:]
        for i in family:
            parent = await db('SELECT parent_id FROM parents WHERE child_id=$1 AND guild_id=$2', i.id, guild_id)
            children = await db('SELECT child_id FROM parents WHERE parent_id=$1 AND guild_id=$2', i.id, guild_id)
            partner = await db('SELECT partner_id FROM marriages WHERE user_id=$1 AND guild_id=$2', i.id, guild_id)

            # Load data into cache
            children = [i['child_id'] for i in children]
            parent_id = parent[0]['parent_id'] if len(parent) > 0 else None
            partner_id = partner[0]['partner_id'] if len(partner) > 0 else None
            f = utils.FamilyTreeMember(
                i.id,
                children=children,
                parent_id=parent_id,
                partner_id=partner_id,
                guild_id=guild_id,
            )

            # Push update via redis
            await re.publish_json('TreeMemberUpdate', f.to_json())

        # Disconnect from database
        await db.disconnect()
        await re.disconnect()

        # Output to user
        await ctx.send(f"Published `{len(family)}` updates.")

    @commands.command()
    @utils.checks.is_server_specific_bot_moderator()
    async def forcemarry(self, ctx:utils.Context, user_a:utils.converters.UserID, user_b:utils.converters.UserID=None):
        """Marries the two specified users"""

        # Correct params
        if user_b is None:
            user_b = ctx.author.id
        if user_a == user_b:
            await ctx.send("You can't marry yourself (but you can be your own parent ;3).")
            return

        # Get users
        me = utils.FamilyTreeMember.get(user_a, ctx.family_guild_id)
        them = utils.FamilyTreeMember.get(user_b, ctx.family_guild_id)

        # See if they have partners
        if me.partner != None or them.partner != None:
            await ctx.send("One of those users already has a partner.")
            return

        # Update database
        async with self.bot.database() as db:
            await db.marry(user_a, user_b, ctx.family_guild_id)
        me._partner = user_b
        them._partner = user_a
        await ctx.send("Consider it done.")

    @commands.command()
    @utils.checks.is_server_specific_bot_moderator()
    async def forcedivorce(self, ctx:utils.Context, user:utils.converters.UserID):
        """Divorces a user from their spouse"""

        # Get user
        me = utils.FamilyTreeMember.get(user, ctx.family_guild_id)
        if not me.partner:
            await ctx.send("That person isn't even married .-.")
            return

        # Update database
        async with self.bot.database() as db:
            await db('DELETE FROM marriages WHERE (user_id=$1 OR partner_id=$1) AND guild_id=$2', user, ctx.family_guild_id)

        # Update cache
        me.partner._partner = None
        me._partner = None
        await ctx.send("Consider it done.")

    @commands.command()
    @utils.checks.is_server_specific_bot_moderator()
    async def forceadopt(self, ctx:utils.Context, parent:utils.converters.UserID, child:utils.converters.UserID=None):
        """Adds the child to the specified parent"""

        # Correct params
        if child is None:
            child = parent
            parent = ctx.author.id

        # Check users
        them = utils.FamilyTreeMember.get(child, ctx.family_guild_id)
        child_name = await self.bot.get_name(child)
        if them.parent:
            await ctx.send(f"`{child_name!s}` already has a parent.")
            return

        # Update database
        async with self.bot.database() as db:
            await db('INSERT INTO parents (parent_id, child_id, guild_id, timestamp) VALUES ($1, $2, $3, $4)', parent, child, ctx.family_guild_id, dt.utcnow())

        # Update cache
        me = utils.FamilyTreeMember.get(parent, ctx.family_guild_id)
        me._children.append(child)
        them._parent = parent
        async with self.bot.redis() as re:
            await re.publish_json('TreeMemberUpdate', me.to_json())
            await re.publish_json('TreeMemberUpdate', them.to_json())
        await ctx.send("Consider it done.")

    @commands.command(aliases=['forceeman'])
    @utils.checks.is_server_specific_bot_moderator()
    async def forceemancipate(self, ctx:utils.Context, user:utils.converters.UserID):
        """Force emancipates a child"""

        # Run checks
        me = utils.FamilyTreeMember.get(user, ctx.family_guild_id)
        if not me.parent:
            await ctx.send("That user doesn't even have a parent .-.")
            return

        # Update database
        async with self.bot.database() as db:
            await db('DELETE FROM parents WHERE child_id=$1 AND guild_id=$2', me.id, me._guild_id)

        # Update cache
        me.parent._children.remove(user)
        parent = me.parent
        me._parent = None
        async with self.bot.redis() as re:
            await re.publish_json('TreeMemberUpdate', me.to_json())
            await re.publish_json('TreeMemberUpdate', parent.to_json())
        await ctx.send("Consider it done.")

    @commands.command()
    @utils.checks.is_bot_administrator()
    async def addvoter(self, ctx:utils.Context, user:utils.converters.UserID):
        """Adds a voter to the database"""

        self.bot.dbl_votes[user] = dt.now()
        async with self.bot.database() as db:
            try:
                await db('INSERT INTO dbl_votes (user_id, timestamp) VALUES ($1, $2)', user, self.bot.dbl_votes[user])
            except asyncpg.UniqueViolationError:
                await db('UPDATE dbl_votes SET timestamp=$2 WHERE user_id=$1', user, self.bot.dbl_votes[user])
        await ctx.send("Consider it done.")

    @commands.command(aliases=['addblogpost'])
    @utils.checks.is_bot_administrator()
    async def createblogpost(self, ctx:utils.Context, url:str, title:str, *, content:str=None):
        """Adds a blog post to the database"""

        if content is None:
            return await ctx.send("You can't send no content.")
        verb = "Created"
        async with self.bot.database() as db:
            try:
                await db("INSERT INTO blog_posts VALUES ($1, $2, $3, NOW(), $4)", url, title, content, ctx.author.id)
            except asyncpg.UniqueViolationError:
                await db("UPDATE blog_posts SET url=$1, title=$2, body=$3, created_at=NOW(), author_id=$4 WHERE url=$1", url, title, content, ctx.author.id)
                verb = "Updated"
        await ctx.send(f"{verb} blog post: https://marriagebot.xyz/blog/{url}", embeddify=False)

    @commands.command()
    @utils.checks.is_bot_administrator()
    async def createredirect(self, ctx:utils.Context, code:str, redirect:str):
        """Adds a redirect to the database"""

        async with self.bot.database() as db:
            await db("INSERT INTO redirects VALUES ($1, $2)", code, redirect)
        await ctx.send(f"Created redirect: https://marriagebot.xyz/r/{code}", embeddify=False)


def setup(bot:utils.CustomBot):
    x = ModeratorOnly(bot)
    bot.add_cog(x)

