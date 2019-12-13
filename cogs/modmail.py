import asyncio
from datetime import datetime
from itertools import zip_longest
from typing import Optional, Union
from types import SimpleNamespace

import discord
from discord.ext import commands
from discord.utils import escape_markdown, escape_mentions

from dateutil import parser
from natural.date import duration

from core import checks
from core.models import PermissionLevel, getLogger
from core.paginator import EmbedPaginatorSession
from core.time import UserFriendlyTime, human_timedelta
from core.utils import (
    format_preview,
    User,
    create_not_found_embed,
    format_description,
    trigger_typing,
)

logger = getLogger(__name__)


class Modmail(commands.Cog):
    """Commands directly related to Modmail functionality."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @trigger_typing
    @checks.has_permissions(PermissionLevel.OWNER)
    async def setup(self, ctx):
        """
        Sets up a server for Modmail.

        You only need to run this command
        once after configuring Modmail.
        """

        if ctx.guild != self.bot.modmail_guild:
            return await ctx.send(
                _("You can only setup in the Modmail guild: {guild_name}.".format(guild_name=self.bot.modmail_guild))
            )

        if self.bot.main_category is not None:
            logger.debug("Can't re-setup server, main_category is found.")
            return await ctx.send(_("{guild_name} is already set up.").format(guild_name=self.bot.modmail_guild))

        if self.bot.modmail_guild is None:
            embed = discord.Embed(
                title=_("Error"),
                description=_("Modmail functioning guild not found."),
                color=self.bot.error_color,
            )
            return await ctx.send(embed=embed)

        overwrites = {
            self.bot.modmail_guild.default_role: discord.PermissionOverwrite(
                read_messages=False
            ),
            self.bot.modmail_guild.me: discord.PermissionOverwrite(read_messages=True),
        }

        for level in PermissionLevel:
            if level <= PermissionLevel.REGULAR:
                continue
            permissions = self.bot.config["level_permissions"].get(level.name, [])
            for perm in permissions:
                perm = int(perm)
                if perm == -1:
                    key = self.bot.modmail_guild.default_role
                else:
                    key = self.bot.modmail_guild.get_member(perm)
                    if key is None:
                        key = self.bot.modmail_guild.get_role(perm)
                if key is not None:
                    logger.info("Granting %s access to Modmail category.", key.name)
                    overwrites[key] = discord.PermissionOverwrite(read_messages=True)

        category = await self.bot.modmail_guild.create_category(
            name="Modmail", overwrites=overwrites
        )

        await category.edit(position=0)

        log_channel = await self.bot.modmail_guild.create_text_channel(
            name="bot-logs", category=category
        )

        embed = discord.Embed(
            title=_("Friendly Reminder"),
            description=_("You may use the `{prefix}config set log_channel_id "
                          "<channel-id>` command to set up a custom log channel, then you can delete this default "
                          "{log_channel} log channel.").format(prefix=self.bot.prefix, log_channel=log_channel.mention),
            color=self.bot.main_color,
        )

        embed.add_field(
            name=_("Thanks for using the bot!"),
            value=_("If you like what you see, consider giving the "
                    "[repo a star](https://github.com/kyb3r/modmail) :star: or if you are "
                    "feeling generous, check us out on [Patreon](https://patreon.com/kyber)!"),
        )

        embed.set_footer(
            text=_('Type "{prefix}help" for a complete list of commands.').format(prefix=self.bot.prefix)
        )
        await log_channel.send(embed=embed)

        self.bot.config["main_category_id"] = category.id
        self.bot.config["log_channel_id"] = log_channel.id

        await self.bot.config.update()
        await ctx.send(
            _("**Successfully set up server.**\n"
              "Consider setting permission levels "
              "to give access to roles or users the ability to use Modmail.\n\n"
              "Type:\n- `{prefix}permissions` and `{prefix}permissions add` "
              "for more info on setting permissions.\n"
              "- `{prefix}config help` for a list of available customizations.").format(prefix=self.bot.prefix)
        )

        if (
            not self.bot.config["command_permissions"]
            and not self.bot.config["level_permissions"]
        ):
            await self.bot.update_perms(PermissionLevel.REGULAR, -1)
            for owner_ids in self.bot.owner_ids:
                await self.bot.update_perms(PermissionLevel.OWNER, owner_ids)

    @commands.group(aliases=["snippets"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet(self, ctx, *, name: str.lower = None):
        """
        Create pre-defined messages for use in threads.

        When `{prefix}snippet` is used by itself, this will retrieve
        a list of snippets that are currently set. `{prefix}snippet-name` will show what the
        snippet point to.

        To create a snippet:
        - `{prefix}snippet add snippet-name A pre-defined text.`

        You can use your snippet in a thread channel
        with `{prefix}snippet-name`, the message "A pre-defined text."
        will be sent to the recipient.

        Currently, there is not a built-in anonymous snippet command; however, a workaround
        is available using `{prefix}alias`. Here is how:
        - `{prefix}alias add snippet-name anonreply A pre-defined anonymous text.`

        See also `{prefix}alias`.
        """

        if name is not None:
            val = self.bot.snippets.get(name)
            if val is None:
                embed = create_not_found_embed(
                    name, self.bot.snippets.keys(), "Snippet"
                )
                return await ctx.send(embed=embed)
            return await ctx.send(escape_mentions(val))

        if not self.bot.snippets:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=_("You dont have any snippets at the moment."),
            )
            embed.set_footer(
                text=_("Do {prefix}help snippet for more commands.").format(prefix=self.bot.prefix)
            )
            embed.set_author(name=_("Snippets"), icon_url=ctx.guild.icon_url)
            return await ctx.send(embed=embed)

        embeds = []

        for i, names in enumerate(
            zip_longest(*(iter(sorted(self.bot.snippets)),) * 15)
        ):
            description = format_description(i, names)
            embed = discord.Embed(color=self.bot.main_color, description=description)
            embed.set_author(name=_("Snippets"), icon_url=ctx.guild.icon_url)
            embeds.append(embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @snippet.command(name="raw")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet_raw(self, ctx, *, name: str.lower):
        """
        View the raw content of a snippet.
        """
        val = self.bot.snippets.get(name)
        if val is None:
            embed = create_not_found_embed(name, self.bot.snippets.keys(), _("Snippet"))
            return await ctx.send(embed=embed)
        return await ctx.send(escape_markdown(escape_mentions(val)).replace("<", "\\<"))

    @snippet.command(name="add")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet_add(self, ctx, name: str.lower, *, value: commands.clean_content):
        """
        Add a snippet.

        To add a multi-word snippet name, use quotes: ```
        {prefix}snippet add "two word" this is a two word snippet.
        ```
        """
        if name in self.bot.snippets:
            embed = discord.Embed(
                title=_("Error"),
                color=self.bot.error_color,
                description=_("Snippet `{name}` already exists.").format(name=name),
            )
            return await ctx.send(embed=embed)

        if name in self.bot.aliases:
            embed = discord.Embed(
                title=_("Error"),
                color=self.bot.error_color,
                description=_("An alias with the same name already exists: `{name}`.").format(name=name),
            )
            return await ctx.send(embed=embed)

        if len(name) > 120:
            embed = discord.Embed(
                title=_("Error"),
                color=self.bot.error_color,
                description=_("Snippet names cannot be longer than 120 characters."),
            )
            return await ctx.send(embed=embed)

        self.bot.snippets[name] = value
        await self.bot.config.update()

        embed = discord.Embed(
            title="Added snippet",
            color=self.bot.main_color,
            description=f"Successfully created snippet.",
        )
        return await ctx.send(embed=embed)

    @snippet.command(name="remove", aliases=["del", "delete"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet_remove(self, ctx, *, name: str.lower):
        """Remove a snippet."""

        if name in self.bot.snippets:
            embed = discord.Embed(
                title=_("Removed snippet"),
                color=self.bot.main_color,
                description=_("Snippet `{name}` is now deleted.").format(name=name),
            )
            self.bot.snippets.pop(name)
            await self.bot.config.update()
        else:
            embed = create_not_found_embed(name, self.bot.snippets.keys(), "Snippet")
        await ctx.send(embed=embed)

    @snippet.command(name="edit")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet_edit(self, ctx, name: str.lower, *, value):
        """
        Edit a snippet.

        To edit a multi-word snippet name, use quotes: ```
        {prefix}snippet edit "two word" this is a new two word snippet.
        ```
        """
        if name in self.bot.snippets:
            self.bot.snippets[name] = value
            await self.bot.config.update()

            embed = discord.Embed(
                title=_("Edited snippet"),
                color=self.bot.main_color,
                description=f_('`{name}` will now send "{value}".'),
            )
        else:
            embed = create_not_found_embed(name, self.bot.snippets.keys(), "Snippet")
        await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @checks.thread_only()
    async def move(
        self, ctx, category: discord.CategoryChannel, *, specifics: str = None
    ):
        """
        Move a thread to another category.

        `category` may be a category ID, mention, or name.
        `specifics` is a string which takes in arguments on how to perform the move. Ex: "silently"
        """
        thread = ctx.thread
        silent = False

        if specifics:
            silent_words = ["silent", "silently"]
            silent = any(word in silent_words for word in specifics.split())

        await thread.channel.edit(category=category, sync_permissions=True)

        if self.bot.config["thread_move_notify"] and not silent:
            embed = discord.Embed(
                title=_("Thread Moved"),
                description=self.bot.config["thread_move_response"],
                color=self.bot.main_color,
            )
            await thread.recipient.send(embed=embed)

        sent_emoji, _ = await self.bot.retrieve_emoji()
        try:
            await ctx.message.add_reaction(sent_emoji)
        except (discord.HTTPException, discord.InvalidArgument):
            pass

    async def send_scheduled_close_message(self, ctx, after, silent=False):
        human_delta = human_timedelta(after.dt)

        silent = "*silently* " if silent else ""

        embed = discord.Embed(
            title=_("Scheduled close"),
            description=_("This thread will close {silent}in {time}.").format(silent=silent, time=human_delta),
            color=self.bot.error_color,
        )

        if after.arg and not silent:
            embed.add_field(name=_("Message"), value=after.arg)

        embed.set_footer(
            text=_("Closing will be cancelled if a thread message is sent.")
        )
        embed.timestamp = after.dt

        await ctx.send(embed=embed)

    @commands.command(usage="[after] [close message]")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def close(self, ctx, *, after: UserFriendlyTime = None):
        """
        Close the current thread.

        Close after a period of time:
        - `{prefix}close in 5 hours`
        - `{prefix}close 2m30s`

        Custom close messages:
        - `{prefix}close 2 hours The issue has been resolved.`
        - `{prefix}close We will contact you once we find out more.`

        Silently close a thread (no message)
        - `{prefix}close silently`
        - `{prefix}close in 10m silently`

        Stop a thread from closing:
        - `{prefix}close cancel`
        """

        thread = ctx.thread

        now = datetime.utcnow()

        close_after = (after.dt - now).total_seconds() if after else 0
        message = after.arg if after else None
        silent = str(message).lower() in {_("silent"), _("silently")}
        cancel = str(message).lower() == _("cancel")

        if cancel:

            if thread.close_task is not None or thread.auto_close_task is not None:
                await thread.cancel_closure(all=True)
                embed = discord.Embed(
                    color=self.bot.error_color,
                    description=_("Scheduled close has been cancelled."),
                )
            else:
                embed = discord.Embed(
                    color=self.bot.error_color,
                    description=_("This thread has not already been scheduled to close."),
                )

            return await ctx.send(embed=embed)

        if after and after.dt > now:
            await self.send_scheduled_close_message(ctx, after, silent)

        await thread.close(
            closer=ctx.author, after=close_after, message=message, silent=silent
        )

    @staticmethod
    def parse_user_or_role(ctx, user_or_role):
        mention = None
        if user_or_role is None:
            mention = ctx.author.mention
        elif hasattr(user_or_role, "mention"):
            mention = user_or_role.mention
        elif user_or_role in {"here", "everyone", "@here", "@everyone"}:
            mention = "@" + user_or_role.lstrip("@")
        return mention

    @commands.command(aliases=["alert"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def notify(
        self, ctx, *, user_or_role: Union[discord.Role, User, str.lower, None] = None
    ):
        """
        Notify a user or role when the next thread message received.

        Once a thread message is received, `user_or_role` will only be pinged once.

        Leave `user_or_role` empty to notify yourself.
        `@here` and `@everyone` can be substituted with `here` and `everyone`.
        `user_or_role` may be a user ID, mention, name. role ID, mention, name, "everyone", or "here".
        """
        mention = self.parse_user_or_role(ctx, user_or_role)
        if mention is None:
            raise commands.BadArgument(f"{user_or_role} is not a valid role.")

        thread = ctx.thread

        if str(thread.id) not in self.bot.config["notification_squad"]:
            self.bot.config["notification_squad"][str(thread.id)] = []

        mentions = self.bot.config["notification_squad"][str(thread.id)]

        if mention in mentions:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=_("{mention} is already going to be mentioned.").format(mention=mention),
            )
        else:
            mentions.append(mention)
            await self.bot.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=_("{mention} will be mentioned "
                              "on the next message received.").format(mention=mention),
            )
        return await ctx.send(embed=embed)

    @commands.command(aliases=["unalert"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def unnotify(
        self, ctx, *, user_or_role: Union[discord.Role, User, str.lower, None] = None
    ):
        """
        Un-notify a user, role, or yourself from a thread.

        Leave `user_or_role` empty to un-notify yourself.
        `@here` and `@everyone` can be substituted with `here` and `everyone`.
        `user_or_role` may be a user ID, mention, name, role ID, mention, name, "everyone", or "here".
        """
        mention = self.parse_user_or_role(ctx, user_or_role)
        if mention is None:
            mention = f"`{user_or_role}`"

        thread = ctx.thread

        if str(thread.id) not in self.bot.config["notification_squad"]:
            self.bot.config["notification_squad"][str(thread.id)] = []

        mentions = self.bot.config["notification_squad"][str(thread.id)]

        if mention not in mentions:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=_("{mention} does not have a pending notification.").format(mention=mention),
            )
        else:
            mentions.remove(mention)
            await self.bot.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=_("{mention} will no longer be notified.").format(mention=mention),
            )
        return await ctx.send(embed=embed)

    @commands.command(aliases=["sub"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def subscribe(
        self, ctx, *, user_or_role: Union[discord.Role, User, str.lower, None] = None
    ):
        """
        Notify a user, role, or yourself for every thread message received.

        You will be pinged for every thread message received until you unsubscribe.

        Leave `user_or_role` empty to subscribe yourself.
        `@here` and `@everyone` can be substituted with `here` and `everyone`.
        `user_or_role` may be a user ID, mention, name, role ID, mention, name, "everyone", or "here".
        """
        mention = self.parse_user_or_role(ctx, user_or_role)
        if mention is None:
            raise commands.BadArgument(f"{user_or_role} is not a valid role.")

        thread = ctx.thread

        if str(thread.id) not in self.bot.config["subscriptions"]:
            self.bot.config["subscriptions"][str(thread.id)] = []

        mentions = self.bot.config["subscriptions"][str(thread.id)]

        if mention in mentions:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=_("{mention} is already subscribed to this thread.").format(mention=mention),
            )
        else:
            mentions.append(mention)
            await self.bot.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=_("{mention} will now be "
                              "notified of all messages received.").format(mention=mention),
            )
        return await ctx.send(embed=embed)

    @commands.command(aliases=["unsub"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def unsubscribe(
        self, ctx, *, user_or_role: Union[discord.Role, User, str.lower, None] = None
    ):
        """
        Unsubscribe a user, role, or yourself from a thread.

        Leave `user_or_role` empty to unsubscribe yourself.
        `@here` and `@everyone` can be substituted with `here` and `everyone`.
        `user_or_role` may be a user ID, mention, name, role ID, mention, name, "everyone", or "here".
        """
        mention = self.parse_user_or_role(ctx, user_or_role)
        if mention is None:
            mention = f"`{user_or_role}`"

        thread = ctx.thread

        if str(thread.id) not in self.bot.config["subscriptions"]:
            self.bot.config["subscriptions"][str(thread.id)] = []

        mentions = self.bot.config["subscriptions"][str(thread.id)]

        if mention not in mentions:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=_("{mention} is not already subscribed to this thread.").format(mention=mention),
            )
        else:
            mentions.remove(mention)
            await self.bot.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=_("{mention} is now unsubscribed to this thread.").format(mention=mention),
            )
        return await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def nsfw(self, ctx):
        """Flags a Modmail thread as NSFW (not safe for work)."""
        await ctx.channel.edit(nsfw=True)
        sent_emoji, _ = await self.bot.retrieve_emoji()
        try:
            await ctx.message.add_reaction(sent_emoji)
        except (discord.HTTPException, discord.InvalidArgument):
            pass

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def sfw(self, ctx):
        """Flags a Modmail thread as SFW (safe for work)."""
        await ctx.channel.edit(nsfw=False)
        sent_emoji, _ = await self.bot.retrieve_emoji()
        try:
            await ctx.message.add_reaction(sent_emoji)
        except (discord.HTTPException, discord.InvalidArgument):
            pass

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def loglink(self, ctx):
        """Retrieves the link to the current thread's logs."""
        log_link = await self.bot.api.get_log_link(ctx.channel.id)
        await ctx.send(
            embed=discord.Embed(color=self.bot.main_color, description=log_link)
        )

    def format_log_embeds(self, logs, avatar_url):
        embeds = []
        logs = tuple(logs)
        title = f"Total Results Found ({len(logs)})"

        for entry in logs:
            created_at = parser.parse(entry["created_at"])

            prefix = self.bot.config["log_url_prefix"].strip("/")
            if prefix == "NONE":
                prefix = ""
            log_url = f"{self.bot.config['log_url'].strip('/')}{'/' + prefix if prefix else ''}/{entry['key']}"

            username = entry["recipient"]["name"] + "#"
            username += entry["recipient"]["discriminator"]

            embed = discord.Embed(color=self.bot.main_color, timestamp=created_at)
            embed.set_author(
                name=f"{title} - {username}", icon_url=avatar_url, url=log_url
            )
            embed.url = log_url
            embed.add_field(
                name="Created", value=duration(created_at, now=datetime.utcnow())
            )
            closer = entry.get("closer")
            if closer is None:
                closer_msg = _("Unknown")
            else:
                closer_msg = f"<@{closer['id']}>"
            embed.add_field(name=_("Closed By"), value=closer_msg)

            if entry["recipient"]["id"] != entry["creator"]["id"]:
                embed.add_field(name=_("Created by"), value=f"<@{entry['creator']['id']}>")

            embed.add_field(
                name=_("Preview"), value=format_preview(entry["messages"]), inline=False
            )

            if closer is not None:
                # BUG: Currently, logviewer can't display logs without a closer.
                embed.add_field(name=_("Link"), value=log_url)
            else:
                logger.debug("Invalid log entry: no closer.")
                embed.add_field(name=_("Log Key"), value=f"`{entry['key']}`")

            embed.set_footer(text=_("Recipient ID") + ": " + str(entry["recipient"]["id"]))
            embeds.append(embed)
        return embeds

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def logs(self, ctx, *, user: User = None):
        """
        Get previous Modmail thread logs of a member.

        Leave `user` blank when this command is used within a
        thread channel to show logs for the current recipient.
        `user` may be a user ID, mention, or name.
        """

        await ctx.trigger_typing()

        if not user:
            thread = ctx.thread
            if not thread:
                raise commands.MissingRequiredArgument(SimpleNamespace(name="member"))
            user = thread.recipient

        default_avatar = "https://cdn.discordapp.com/embed/avatars/0.png"
        icon_url = getattr(user, "avatar_url", default_avatar)

        logs = await self.bot.api.get_user_logs(user.id)

        if not any(not log["open"] for log in logs):
            embed = discord.Embed(
                color=self.bot.error_color,
                description=_("This user does not have any previous logs."),
            )
            return await ctx.send(embed=embed)

        logs = reversed([e for e in logs if not e["open"]])

        embeds = self.format_log_embeds(logs, avatar_url=icon_url)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @logs.command(name="closed-by", aliases=["closeby"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def logs_closed_by(self, ctx, *, user: User = None):
        """
        Get all logs closed by the specified user.

        If no `user` is provided, the user will be the person who sent this command.
        `user` may be a user ID, mention, or name.
        """
        user = user if user is not None else ctx.author

        query = {
            "guild_id": str(self.bot.guild_id),
            "open": False,
            "closer.id": str(user.id),
        }

        projection = {"messages": {"$slice": 5}}

        entries = await self.bot.db.logs.find(query, projection).to_list(None)

        embeds = self.format_log_embeds(entries, avatar_url=self.bot.guild.icon_url)

        if not embeds:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=_("No log entries have been found for that query"),
            )
            return await ctx.send(embed=embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @logs.command(name="delete", aliases=["wipe"])
    @checks.has_permissions(PermissionLevel.OWNER)
    async def logs_delete(self, ctx, key_or_link: str):
        """
        Wipe a log entry from the database.
        """
        key = key_or_link.split("/")[-1]

        success = await self.bot.api.delete_log_entry(key)

        if not success:
            embed = discord.Embed(
                title=_("Error"),
                description=_("Log entry `{key}` not found.").format(key=key),
                color=self.bot.error_color,
            )
        else:
            embed = discord.Embed(
                title="Success",
                description=_("Log entry `{key}` successfully deleted.").format(key=key),
                color=self.bot.main_color,
            )

        await ctx.send(embed=embed)

    @logs.command(name="responded")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def logs_responded(self, ctx, *, user: User = None):
        """
        Get all logs where the specified user has responded at least once.

        If no `user` is provided, the user will be the person who sent this command.
        `user` may be a user ID, mention, or name.
        """
        user = user if user is not None else ctx.author

        entries = await self.bot.api.get_responded_logs(user.id)

        embeds = self.format_log_embeds(entries, avatar_url=self.bot.guild.icon_url)

        if not embeds:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=_("{mention} has not responded to any threads.").format(mention=getattr(user, 'mention', user.id)),
            )
            return await ctx.send(embed=embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @logs.command(name="search", aliases=["find"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def logs_search(self, ctx, limit: Optional[int] = None, *, query):
        """
        Retrieve all logs that contain messages with your query.

        Provide a `limit` to specify the maximum number of logs the bot should find.
        """

        await ctx.trigger_typing()

        query = {
            "guild_id": str(self.bot.guild_id),
            "open": False,
            "$text": {"$search": f'"{query}"'},
        }

        projection = {"messages": {"$slice": 5}}

        entries = await self.bot.db.logs.find(query, projection).to_list(limit)

        embeds = self.format_log_embeds(entries, avatar_url=self.bot.guild.icon_url)

        if not embeds:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=_("No log entries have been found for that query."),
            )
            return await ctx.send(embed=embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def reply(self, ctx, *, msg: str = ""):
        """
        Reply to a Modmail thread.

        Supports attachments and images as well as
        automatically embedding image URLs.
        """
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.thread.reply(ctx.message)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def anonreply(self, ctx, *, msg: str = ""):
        """
        Reply to a thread anonymously.

        You can edit the anonymous user's name,
        avatar and tag using the config command.

        Edit the `anon_username`, `anon_avatar_url`
        and `anon_tag` config variables to do so.
        """
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.thread.reply(ctx.message, anonymous=True)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def note(self, ctx, *, msg: str = ""):
        """
        Take a note about the current thread.

        Useful for noting context.
        """
        ctx.message.content = msg
        async with ctx.typing():
            msg = await ctx.thread.note(ctx.message)
            await msg.pin()

    async def find_linked_message(self, ctx, message_id):
        linked_message_id = None

        async for msg in ctx.channel.history():
            if message_id is None and msg.embeds:
                embed = msg.embeds[0]
                if embed.color.value != self.bot.mod_color or not embed.author.url:
                    continue
                # TODO: use regex to find the linked message id
                linked_message_id = str(embed.author.url).split("/")[-1]
                break
            elif message_id and msg.id == message_id:
                url = msg.embeds[0].author.url
                linked_message_id = str(url).split("/")[-1]
                break

        return linked_message_id

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def edit(self, ctx, message_id: Optional[int] = None, *, message: str):
        """
        Edit a message that was sent using the reply or anonreply command.

        If no `message_id` is provided, the
        last message sent by a staff will be edited.
        """
        thread = ctx.thread

        linked_message_id = await self.find_linked_message(ctx, message_id)

        if linked_message_id is None:
            return await ctx.send(
                embed=discord.Embed(
                    title=_("Failed"),
                    description=_("Cannot find a message to edit."),
                    color=self.bot.error_color,
                )
            )

        await asyncio.gather(
            thread.edit_message(linked_message_id, message),
            self.bot.api.edit_message(linked_message_id, message),
        )

        sent_emoji, _ = await self.bot.retrieve_emoji()
        try:
            await ctx.message.add_reaction(sent_emoji)
        except (discord.HTTPException, discord.InvalidArgument):
            pass

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def contact(
        self,
        ctx,
        category: Optional[discord.CategoryChannel] = None,
        *,
        user: Union[discord.Member, discord.User],
    ):
        """
        Create a thread with a specified member.

        If `category` is specified, the thread
        will be created in that specified category.

        `category`, if specified, may be a category ID, mention, or name.
        `user` may be a user ID, mention, or name.
        """

        if user.bot:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=_("Cannot start a thread with a bot."),
            )
            return await ctx.send(embed=embed)

        exists = await self.bot.threads.find(recipient=user)
        if exists:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=_("A thread for this user already "
                              "exists in {mention}.").format(mention=exists.channel.mention),
            )
            await ctx.channel.send(embed=embed)

        else:
            thread = self.bot.threads.create(
                user, creator=ctx.author, category=category
            )
            if self.bot.config["dm_disabled"] >= 1:
                logger.info("Contacting user %s when Modmail DM is disabled.", user)

            embed = discord.Embed(
                title=_("Created Thread"),
                description=_("Thread started by {author_mention} "
                              "for {user_mention}.").format(author_mention=ctx.author.mention, user_mention=user.mention),
                color=self.bot.main_color,
            )
            await thread.wait_until_ready()
            await thread.channel.send(embed=embed)
            sent_emoji, _ = await self.bot.retrieve_emoji()
            try:
                await ctx.message.add_reaction(sent_emoji)
            except (discord.HTTPException, discord.InvalidArgument):
                pass
            await asyncio.sleep(3)
            await ctx.message.delete()

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @trigger_typing
    async def blocked(self, ctx):
        """Retrieve a list of blocked users."""

        embeds = [
            discord.Embed(
                title=_("Blocked Users"), color=self.bot.main_color, description=""
            )
        ]

        users = []

        for id_, reason in self.bot.blocked_users.items():
            user = self.bot.get_user(int(id_))
            if user:
                users.append((user.mention, reason))
            else:
                try:
                    user = await self.bot.fetch_user(id_)
                    users.append((user.mention, reason))
                except discord.NotFound:
                    users.append((id_, reason))

        if users:
            embed = embeds[0]

            for mention, reason in users:
                line = mention + f" - {reason or _('No Reason Provided')}\n"
                if len(embed.description) + len(line) > 2048:
                    embed = discord.Embed(
                        title=_("Blocked Users") + " " + _("(Continued)"),
                        color=self.bot.main_color,
                        description=line,
                    )
                    embeds.append(embed)
                else:
                    embed.description += line
        else:
            embeds[0].description = _("Currently there are no blocked users.")

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @blocked.command(name="whitelist")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @trigger_typing
    async def blocked_whitelist(self, ctx, *, user: User = None):
        """
        Whitelist or un-whitelist a user from getting blocked.

        Useful for preventing users from getting blocked by account_age/guild_age restrictions.
        """
        if user is None:
            thread = ctx.thread
            if thread:
                user = thread.recipient
            else:
                return await ctx.send_help(ctx.command)

        mention = getattr(user, "mention", f"`{user.id}`")
        msg = ""

        if str(user.id) in self.bot.blocked_whitelisted_users:
            embed = discord.Embed(
                title=_("Success"),
                description=_("{mention} is no longer whitelisted.").format(mention=mention),
                color=self.bot.main_color,
            )
            self.bot.blocked_whitelisted_users.remove(str(user.id))
            return await ctx.send(embed=embed)

        self.bot.blocked_whitelisted_users.append(str(user.id))

        if str(user.id) in self.bot.blocked_users:
            msg = self.bot.blocked_users.get(str(user.id)) or ""
            self.bot.blocked_users.pop(str(user.id))

        await self.bot.config.update()

        if msg.startswith("System Message: "):
            # If the user is blocked internally (for example: below minimum account age)
            # Show an extended message stating the original internal message
            reason = msg[16:].strip().rstrip(".")
            embed = discord.Embed(
                title="Success",
                description=_("{mention} was previously blocked internally for "
                              '"{reason}". {mention} is now whitelisted.').format(mention=mention, reason=reason),
                color=self.bot.main_color,
            )
        else:
            embed = discord.Embed(
                title="Success",
                color=self.bot.main_color,
                description=f"{mention} is now whitelisted.",
            )

        return await ctx.send(embed=embed)

    @commands.command(usage="[user] [duration] [close message]")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @trigger_typing
    async def block(
        self, ctx, user: Optional[User] = None, *, after: UserFriendlyTime = None
    ):
        """
        Block a user from using Modmail.

        You may choose to set a time as to when the user will automatically be unblocked.

        Leave `user` blank when this command is used within a
        thread channel to block the current recipient.
        `user` may be a user ID, mention, or name.
        `duration` may be a simple "human-readable" time text. See `{prefix}help close` for examples.
        """

        if user is None:
            thread = ctx.thread
            if thread:
                user = thread.recipient
            elif after is None:
                raise commands.MissingRequiredArgument(SimpleNamespace(name="user"))
            else:
                raise commands.BadArgument(f'User "{after.arg}" not found')

        mention = getattr(user, "mention", f"`{user.id}`")

        if str(user.id) in self.bot.blocked_whitelisted_users:
            embed = discord.Embed(
                title=_("Error"),
                description=_("Cannot block {mention}, user is whitelisted.").format(mention=mention),
                color=self.bot.error_color,
            )
            return await ctx.send(embed=embed)

        reason = f"by {escape_markdown(ctx.author.name)}#{ctx.author.discriminator}"

        if after is not None:
            if "%" in reason:
                raise commands.BadArgument('The reason contains illegal character "%".')
            if after.arg:
                reason += f" for `{after.arg}`"
            if after.dt > after.now:
                reason += f" until {after.dt.isoformat()}"

        reason += "."

        msg = self.bot.blocked_users.get(str(user.id))
        if msg is None:
            msg = ""

        if str(user.id) in self.bot.blocked_users and msg:
            old_reason = msg.strip().rstrip(".")
            embed = discord.Embed(
                title=_("Success"),
                description=_("{mention} was previously blocked "
                              "{old_reason}.\n{mention} is now blocked {reason}").format(mention=mention, old_reason=old_reason, reason=reason),
                color=self.bot.main_color,
            )
        else:
            embed = discord.Embed(
                title=_("Success"),
                color=self.bot.main_color,
                description=_("{mention} is now blocked {reason}").format(mention=mention, reason=reason),
            )
        self.bot.blocked_users[str(user.id)] = reason
        await self.bot.config.update()

        return await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @trigger_typing
    async def unblock(self, ctx, *, user: User = None):
        """
        Unblock a user from using Modmail.

        Leave `user` blank when this command is used within a
        thread channel to unblock the current recipient.
        `user` may be a user ID, mention, or name.
        """

        if user is None:
            thread = ctx.thread
            if thread:
                user = thread.recipient
            else:
                raise commands.MissingRequiredArgument(SimpleNamespace(name="user"))

        mention = getattr(user, "mention", f"`{user.id}`")
        name = getattr(user, "name", f"`{user.id}`")

        if str(user.id) in self.bot.blocked_users:
            msg = self.bot.blocked_users.pop(str(user.id)) or ""
            await self.bot.config.update()

            if msg.startswith("System Message: "):
                # If the user is blocked internally (for example: below minimum account age)
                # Show an extended message stating the original internal message
                reason = msg[16:].strip().rstrip(".") or "no reason"
                embed = discord.Embed(
                    title="Success",
                    description=_("{mention} was previously blocked internally "
                                  "{reason}.\n{mention} is no longer blocked.").format(mention=mention, reason=reason),
                    color=self.bot.main_color,
                )
                embed.set_footer(
                    text=_("However, if the original system block reason still applies, "
                           "{name} will be automatically blocked again. Use "
                           '"{self.bot.prefix}blocked whitelist {user.id}" to whitelist the user.').format(
                                name=name, prefix=self.bot.prefix, user_id=user.id
                            )
                )
            else:
                embed = discord.Embed(
                    title=_("Success"),
                    color=self.bot.main_color,
                    description=_("{mention} is no longer blocked.").format(mention=mention),
                )
        else:
            embed = discord.Embed(
                title=_("Error"),
                description=_("{mention} is not blocked.").format(mention=mention),
                color=self.bot.error_color,
            )

        return await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def delete(self, ctx, message_id: Optional[int] = None):
        """
        Delete a message that was sent using the reply command or a note.

        Deletes the previous message, unless a message ID is provided,
        which in that case, deletes the message with that message ID.

        Notes can only be deleted when a note ID is provided.
        """
        thread = ctx.thread

        if message_id is not None:
            try:
                message_id = int(message_id)
            except ValueError:
                raise commands.BadArgument(
                    "An integer message ID needs to be specified."
                )

        linked_message_id = await self.find_linked_message(ctx, message_id)

        if linked_message_id is None:
            return await ctx.send(
                embed=discord.Embed(
                    title=_("Failed"),
                    description=_("Cannot find a message to delete."),
                    color=self.bot.error_color,
                )
            )

        await thread.delete_message(linked_message_id)
        sent_emoji, _ = await self.bot.retrieve_emoji()
        try:
            await ctx.message.add_reaction(sent_emoji)
        except (discord.HTTPException, discord.InvalidArgument):
            pass

    @commands.command()
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def enable(self, ctx):
        """
        Re-enables DM functionalities of Modmail.

        Undo's the `{prefix}disable` command, all DM will be relayed after running this command.
        """
        embed = discord.Embed(
            title=_("Success"),
            description=_("Modmail will now accept all DM messages."),
            color=self.bot.main_color,
        )

        if self.bot.config["dm_disabled"] != 0:
            self.bot.config["dm_disabled"] = 0
            await self.bot.config.update()

        return await ctx.send(embed=embed)

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def disable(self, ctx):
        """
        Stop accepting new Modmail threads.

        No new threads can be created through DM.
        To stop all existing threads from DMing Modmail, do `{prefix}disable all`.
        """
        embed = discord.Embed(
            title=_("Success"),
            description=_("Modmail will not create any new threads."),
            color=self.bot.main_color,
        )
        if self.bot.config["dm_disabled"] < 1:
            self.bot.config["dm_disabled"] = 1
            await self.bot.config.update()

        return await ctx.send(embed=embed)

    @disable.command(name="all")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def disable_all(self, ctx):
        """
        Disables all DM functionalities of Modmail.

        No new threads can be created through DM nor no further DM messages will be relayed.
        """
        embed = discord.Embed(
            title=_("Success"),
            description=_("Modmail will not accept any DM messages."),
            color=self.bot.main_color,
        )

        if self.bot.config["dm_disabled"] != 2:
            self.bot.config["dm_disabled"] = 2
            await self.bot.config.update()

        return await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def isenable(self, ctx):
        """
        Check if the DM functionalities of Modmail is enabled.
        """

        if self.bot.config["dm_disabled"] == 1:
            embed = discord.Embed(
                title=_("New Threads Disabled"),
                description=_("Modmail is not creating new threads."),
                color=self.bot.error_color,
            )
        elif self.bot.config["dm_disabled"] == 2:
            embed = discord.Embed(
                title=_("All DM Disabled"),
                description=_("Modmail is not accepting any DM messages for new and existing threads."),
                color=self.bot.error_color,
            )
        else:
            embed = discord.Embed(
                title=_("Enabled"),
                description=_("Modmail is accepting all DM messages."),
                color=self.bot.main_color,
            )

        return await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(Modmail(bot))
