import nextcord
from nextcord.ext import commands
from nextcord import Interaction, SlashOption
import sqlite3

class GroupChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.gc_enabled = {}

    @commands.Cog.listener()
    async def on_ready(self):
        # Load GC enabled status from database
        conn = sqlite3.connect('gc_settings.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gc_settings (
                guild_id INTEGER PRIMARY KEY,
                gc_enabled INTEGER DEFAULT 1
            )
        ''')
        cursor.execute("SELECT guild_id, gc_enabled FROM gc_settings")
        for row in cursor.fetchall():
            self.gc_enabled[row[0]] = bool(row[1])
        conn.close()

    @nextcord.slash_command(name="gc", description="Group chat commands")
    async def gc(self, interaction: Interaction):
        pass

    @gc.subcommand(name="setup", description="Create a new group chat")
    async def setup(self,
                    interaction: Interaction,
                    gc_name: str = SlashOption(description="Name of the group chat")
                    ):

        # Check if GC is enabled for this server
        guild_id = interaction.guild.id
        if not self.gc_enabled.get(guild_id, True):
            return await interaction.response.send_message("Group chat creation is currently disabled for this server.", ephemeral=True)

        # Get or Create the Category
        category_name = "Group Chats"
        category = nextcord.utils.get(interaction.guild.categories, name=category_name)
        if not category:
            try:
                category = await interaction.guild.create_category(category_name)
            except nextcord.Forbidden:
                return await interaction.response.send_message("I don't have permission to create categories.", ephemeral=True)

        # Create the channel with only the owner having access
        overwrites = {
            interaction.guild.default_role: nextcord.PermissionOverwrite(read_messages=False),
            interaction.user: nextcord.PermissionOverwrite(read_messages=True),
        }

        try:
            channel = await interaction.guild.create_text_channel(
                name=gc_name,
                overwrites=overwrites,
                category=category
            )
        except nextcord.Forbidden:
            return await interaction.response.send_message("I don't have permission to create channels.", ephemeral=True)
        except nextcord.HTTPException as e:
            return await interaction.response.send_message(f"An error occurred while creating the channel: {e}", ephemeral=True)

        # Store the GC owner in the database
        conn = sqlite3.connect('gc_owners.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gc_owners (
                gc_id INTEGER PRIMARY KEY,
                owner_id INTEGER
            )
        ''')
        try:
            cursor.execute("INSERT INTO gc_owners (gc_id, owner_id) VALUES (?, ?)", (channel.id, interaction.user.id))
        except sqlite3.IntegrityError:
            return await interaction.response.send_message("A group chat with that name already exists.", ephemeral=True)
        conn.commit()
        conn.close()

        await interaction.response.send_message(f"Group chat '{gc_name}' created successfully!", ephemeral=True)

    @gc.subcommand(name="add-member", description="Add a member to a group chat (Owner only)")
    async def add_member(self,
                         interaction: Interaction,
                         gc_name: str = SlashOption(description="Name of the group chat"),
                         member: nextcord.Member = SlashOption(description="Member to add")):

        await self._manage_member(interaction, gc_name, member, True)

    @gc.subcommand(name="remove-member", description="Remove a member from a group chat (Owner only)")
    async def remove_member(self,
                            interaction: Interaction,
                            gc_name: str = SlashOption(description="Name of the group chat"),
                            member: nextcord.Member = SlashOption(description="Member to remove")):

        await self._manage_member(interaction, gc_name, member, False)

    @gc.subcommand(name="delete", description="Delete a group chat (Owner or Admin only)")
    async def delete(self,
                     interaction: Interaction,
                     gc_name: str = SlashOption(description="Name of the group chat to delete")):

        channel = nextcord.utils.get(interaction.guild.channels, name=gc_name)
        if not channel:
            return await interaction.response.send_message(f"Group chat '{gc_name}' not found.", ephemeral=True)

        # Check if the user is the owner OR an admin
        if not (await self._check_ownership(interaction, channel) or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("You do not have permission to delete this group chat.", ephemeral=True)

        try:
            await channel.delete()

            # Remove the GC from the database
            conn = sqlite3.connect('gc_owners.db')
            cursor = conn.cursor()
            cursor.execute("DELETE FROM gc_owners WHERE gc_id = ?", (channel.id,))
            conn.commit()
            conn.close()

            await interaction.response.send_message(f"Group chat '{gc_name}' deleted successfully!", ephemeral=True)
        except nextcord.Forbidden:
            return await interaction.response.send_message("I don't have permission to delete this channel.", ephemeral=True)

    @gc.subcommand(name="toggle", description="Enable/Disable group chat creation for this server (Admin only)")
    async def toggle_gc(self, interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("You need administrator permission to use this command.", ephemeral=True)

        guild_id = interaction.guild.id
        self.gc_enabled[guild_id] = not self.gc_enabled.get(guild_id, True)

        # Save the updated status to the database
        conn = sqlite3.connect('gc_settings.db')
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO gc_settings (guild_id, gc_enabled) VALUES (?, ?)", (guild_id, int(self.gc_enabled[guild_id])))
        conn.commit()
        conn.close()

        status = "enabled" if self.gc_enabled[guild_id] else "disabled"
        await interaction.response.send_message(f"Group chat creation has been **{status}** for this server.", ephemeral=True)

    async def _manage_member(self, interaction: Interaction, gc_name: str, member: nextcord.Member, add: bool):
        channel = nextcord.utils.get(interaction.guild.channels, name=gc_name)
        if not channel:
            return await interaction.response.send_message(f"Group chat '{gc_name}' not found.", ephemeral=True)

        if not await self._check_ownership(interaction, channel):
            return await interaction.response.send_message("You are not the owner of this group chat.", ephemeral=True)

        try:
            if add:
                await channel.set_permissions(member, read_messages=True)
                action = "added to"
            else:
                await channel.set_permissions(member, overwrite=None)
                action = "removed from"
            await interaction.response.send_message(f"{member.mention} {action} '{gc_name}' successfully!", ephemeral=True)
        except nextcord.Forbidden:
            return await interaction.response.send_message("I don't have permission to manage this channel.", ephemeral=True)

    async def _check_ownership(self, interaction: Interaction, channel: nextcord.TextChannel):
        conn = sqlite3.connect('gc_owners.db')
        cursor = conn.cursor()
        cursor.execute("SELECT owner_id FROM gc_owners WHERE gc_id = ?", (channel.id,))
        result = cursor.fetchone()
        conn.close()
        if result and result[0] == interaction.user.id:
            return True
        return False

def setup(bot):
    bot.add_cog(GroupChat(bot))