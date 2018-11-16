import discord
import logging
import datetime
import sqlite3
import json 
import re
import asyncio

from sqlite3 import Error
from discord.ext.commands import Bot

TOKEN = 'YOUR TOKEN HERE' # TODO: just read this from a file
BOT_PREFIX = (".")
logging.basicConfig(level=logging.INFO)

client = discord.Client()

bot = Bot(command_prefix=BOT_PREFIX)
bot.remove_command('help')
database = "dailybot.db"
    
async def scheduled_tasks():
    await client.wait_until_ready()
   
    while not client.is_closed:
        
        await asyncio.sleep((60*60)*24) # task runs every hour

#region Listener for On Message Sent
@client.event
async def on_message(message):
    # we do not want the bot to reply to itself
    if message.author == client.user:
        return
    await bot.process_commands(message)
#endregion
@bot.command(pass_context=True)
async def help(context):
    channel_config = get_config(context)
    if channel_config[1]:
        if not can_kick(context.message.author):
            description = "**Here is the list of commands available to you:**\n\n\
                            `.suggest` - Suggest a topic for daily discussion"
        else:
            description = "**Here is the list of commands available to you:**\n\n\
                            `.suggest` - Suggest a topic for daily discussion\n\n\
                            `.daily` - Begin or preview daily discussion questions (moderator only)\n\n\
                            `.ping_role` - Assign a role for the bot to mention daily discussions. (admin only)\n\n\
                            `.assign_channel` - Assign a `suggestions` and a `discussions` channel. (admin only)"
    else:
        description = "It appears that you have not yet configured your server for automated daily discussions!\n\n\
                            You must configure a `suggestions` and a `discussions` channel before continuing.\n\n\
                            Please use the `.assign_channel` command to do so now."
    embed = discord.Embed(colour=discord.Colour(0x7ac3f2), description=description)
    await client.send_message(context.message.channel, embed=embed)

@bot.command(pass_context=True)
async def ping_role(context, role: discord.Role = None):
    if is_admin(context.message.author):
        sql = db_connect()
        current_channel = context.message.channel
        channel_config = get_config(context)
        server_dbid = channel_config[0]
        if channel_config[1]:
            if type(role) is not discord.Role:
                message = "That is not a valid role. You must use specify a role to be mentioned for daily discussions. \n\n ex. `.ping_role @everyone`"
                embed = build_embed(message, "red")
                await client.send_message(current_channel, embed=embed)
                return
            sql.execute("INSERT INTO ping_roles (server, role_id, role_name)\
                        SELECT ?, ?, ? WHERE ? NOT IN (SELECT server FROM ping_roles)", (server_dbid, role.id, role.name, server_dbid))
            sql.execute("UPDATE ping_roles SET role_id = ?, role_name = ?\
                            WHERE server = ?", (role.id, role.name, server_dbid))
            await client.send_message(current_channel, embed=build_embed("You selected " + role.mention + " as your **daily discussion role**.", "blue"))
        else:
            await help.invoke(context)
    else:
        message = "You do not have permission to do that."
        embed = build_embed(message, "red")
        await client.send_message(context.message.channel, embed=embed)

@bot.command(pass_context=True)
async def assign_channel(context, channel_type: str = "", channel: discord.Channel = None):
    
    if is_admin(context.message.author):
        current_channel = context.message.channel
        server = context.message.server
        sql = db_connect()
        res = sql.execute("SELECT id FROM servers WHERE server_id = ?", (server.id,))
        
        server_dbid = res.fetchone()[0]
        if channel_type.lower() == "suggestions":
            target_channel = "suggestion_channel, suggestion_channel_name"
        elif channel_type.lower() == "discussions":
            target_channel = "discussion_channel, discussion_channel_name"
        else:
            message = "You must use either `suggestions` or `discussions` and then mention a channel to listen to. \n\n ex. `.assign_channel suggestions #topic-suggestions`"
            embed = build_embed(message, "red")
            await client.send_message(current_channel, embed=embed)
            return
        sql.execute("INSERT INTO channel_assignments (server, "+ target_channel +")\
                    SELECT ?, ?, ? WHERE ? NOT IN (SELECT server FROM channel_assignments)", (server_dbid, channel.id, channel.name, server_dbid))
        sql.execute("UPDATE channel_assignments SET " + target_channel.split(", ")[0] + " = ?, " + target_channel.split(", ")[1] + " = ?\
                        WHERE server = ?", (channel.id, channel.name, server_dbid))
        await client.send_message(current_channel, embed=build_embed("You selected " + channel.mention + " as your **daily " + channel_type + "** channel.", "blue"))
    else:
        message = "You do not have permission to do that."
        embed = build_embed(message, "red")
        await client.send_message(context.message.channel, embed=embed)
#region .suggest
@bot.command(pass_context=True)
async def suggest(context):
    current_channel = context.message.channel
    channel_config = get_config(context)
    server_dbid = channel_config[0]
    if channel_config[1]:
        sugg_chan = client.get_channel(channel_config[1][1])
        #disc_chan = await client.get_channel(channel_config[1][0])
        if sugg_chan.name in context.message.channel.name:
            suggestion = command_text('.suggest', context.message.content)
            if len(suggestion) > 10:
                embed = discord.Embed(title="Suggested Topic", colour=discord.Colour(0x36b319), description=suggestion)
                embed.set_author(
                    name=context.message.author.display_name + " :: " + context.message.author.name+"#"+context.message.author.discriminator, 
                    icon_url=context.message.author.avatar_url
                )
                bot_message = await client.send_message(context.message.channel, embed=embed)
                await client.add_reaction(bot_message, emoji="👎")
                await client.add_reaction(bot_message, emoji="👍")
                await client.delete_message(context.message)
                return  
            else:
                embed = discord.Embed(colour=discord.Colour(0xC70636), description="Suggestions must be more than 10 characters to be valid.")
                await client.send_message(context.message.channel, embed=embed)
            if not can_kick(context.message.author):
                await client.delete_message(context.message)
    else:
        await help.invoke(context)
#endregion
#region .daily
@bot.command(pass_context=True)
async def daily(context):
    channel_config = get_config(context)
    if channel_config[1]:
        if can_kick(context.message.author):

            debate_channel = client.get_channel(channel_config[1][0])
            suggestions_channel = client.get_channel(channel_config[1][1])
            server_dbid = channel_config[0]
            sql = db_connect()
            res = sql.execute("SELECT role_id FROM ping_roles WHERE server = ?", (server_dbid,))
            ping_role = res.fetchone()
            ping_role_object = None
            if ping_role:
                ping_role_object = discord.utils.get(context.message.server.roles, id=ping_role[0])
                ping_role = ping_role_object.mention
            else:
                ping_role = ""
            counter = 0
            suggestion_pool = []
            
            async for suggestion in client.logs_from(suggestions_channel, limit=500):
                if suggestion.author == client.user:
                    points = 0
                    for vote in suggestion.reactions:
                        if(vote.emoji == '👍'): 
                            points += vote.count
                        elif(vote.emoji == '👎'): 
                            points -= vote.count
                    if points > 0:
                        suggestion_pool.append({'suggestion': suggestion, 'points': points})
                counter += 1
            daily_topics = await client.pins_from(debate_channel)
            for daily_topic in daily_topics:
                counter = 0
                for suggestion in suggestion_pool:
                    if len(daily_topic.embeds) > 0:
                        if daily_topic.embeds[0]['description'].find(suggestion['suggestion'].embeds[0]['description']) > -1:
                            suggestion_pool.pop(counter)
                        counter += 1
                    else:
                        break
            suggestion_pool.sort(key=lambda x: x['points'], reverse=True)

            if 'rank' in command_text('.daily', context.message.content):
                index = 0
                description = ""
                num_queried = min(int(command_text('rank', context.message.content)), len(suggestion_pool)) if command_text('rank', context.message.content) != "" else min(5,len(suggestion_pool))
                
                for suggestion in suggestion_pool:
                    if index >= num_queried:
                        break
                    suggestion_embed = suggestion['suggestion'].embeds[0]
                    author = context.message.server.get_member_named(suggestion_embed['author']['name'].split(":: ")[1])
                    description += ("#"+str(index+1)) + " ("+str(suggestion['points']) + " points): " +suggestion_embed['description'] + " suggested by **" + author.display_name +"** \n--------\n"
                    index+=1
                embed = discord.Embed(colour=discord.Colour(0xD4BA39), description=description)
                await client.send_message(context.message.channel, embed=embed)
            elif 'reminder' in command_text('.daily', context.message.content):
                messages = await client.pins_from(debate_channel)
                embed = messages[0].embeds[0]
                description = embed['description'].replace("**Daily Discussion** **\n\n", "")
                description = description.split("** suggested by")[0]
                description = "**Daily discussion reminder** \n\n**" + description + "**\n\n *You can submit your own daily topics in " + suggestions_channel.mention + " and vote for ones that you like.*"
                embed = discord.Embed(colour=discord.Colour(0xD4BA39), description=description)
                await client.send_message(debate_channel, embed=embed)
            elif 'start' in command_text('.daily', context.message.content):
                if not suggestion_pool:
                    description = "There is no eligible debate topic right now."
                    embed = discord.Embed(colour=discord.Colour(0xD4BA39), description=description)
                    await client.send_message(context.message.channel, embed=embed)
                else:
                    top_suggestion = suggestion_pool[0]['suggestion'].embeds[0]
                    author = context.message.server.get_member_named(top_suggestion['author']['name'].split(":: ")[1])
                    
                    description = top_suggestion['description']
                    embed = discord.Embed(colour=discord.Colour(0xD4BA39), description="**Daily Discussion** **\n\n" + description + "** suggested by " + author.mention + "\n\n *You can submit your own daily topics in " + suggestions_channel.mention + " and vote for ones that you like.*" )
                    if ping_role_object is not None:
                        await client.edit_role(context.message.server, ping_role_object, mentionable=True)
                    bot_message = await client.send_message(debate_channel, content=":bell: " + ping_role, embed=embed)
                    await client.pin_message(bot_message)
                    if ping_role_object is not None:
                        await client.edit_role(context.message.server, ping_role_object, mentionable=False)
            else:
                description = "To use the `.daily` command, you must pass one of the following parameters (ex. `.daily start`):\n\n\
                                `start` - Begins the next daily discussion topic based on the highest-voted, unused suggestion\n\n\
                                `reminder` - The bot writes a message with the current discussion topic.\n\n\
                                `rank` - To be used in conjunction with a number, ie `.daily rank 5` will display the top 5 unused suggestions from the suggestion pool." 
                embed = build_embed(description, "blue")
                await client.send_message(context.message.channel, embed=embed)
        else:
            embed = build_embed("You do not have permission to use this command", "red")
            await client.send_message(context.message.channel, embed=embed)
    else:
        await help.invoke(context)
#endregion
@client.event
async def on_server_join(server):
    sql = db_connect()
    
    owner = await client.get_user_info(server.owner_id)
    sql.execute("INSERT INTO servers (server_id, server_name, owner_id, owner_name) SELECT ?, ?, ?, ? WHERE ? NOT IN (SELECT server_id from servers)", (server.id, server.name, owner.id, owner.display_name+"#"+owner.discriminator, server.id))
    sql.execute("UPDATE servers SET server_name = ?, owner_id = ?, owner_name=? WHERE server_id = ?", (server.name, owner.id, owner.display_name+"#"+owner.discriminator, server.id))

@client.event
async def on_ready():
    print('Logged in as')
    print(client.user.name)
    print(client.user.id)
    print('------')
    #await client.change_presence(game=discord.Game(name='.suggest in #daily-topic-suggestions'))
    sql = db_connect()
    for server in list(client.servers):
        owner = await client.get_user_info(server.owner_id)
        sql.execute("INSERT INTO servers (server_id, server_name, owner_id, owner_name) SELECT ?, ?, ?, ? WHERE ? NOT IN (SELECT server_id from servers)", (server.id, server.name, owner.id, owner.display_name+"#"+owner.discriminator, server.id))
        sql.execute("UPDATE servers SET server_name = ?, owner_id = ?, owner_name=? WHERE server_id = ?", (server.name, owner.id, owner.display_name+"#"+owner.discriminator, server.id))
    

def command_text(command, message):
    if len(message.split(command)) == 2:
        return message.split(command)[1].strip()
    else:
        return ''

def get_config(context):
    server = context.message.server
    sql = db_connect()
    res = sql.execute("SELECT id FROM servers WHERE server_id = ?", (server.id,))
    server_dbid = res.fetchone()[0]
    channel_config = []
    channel_config.append(server_dbid)
    res = None
    res = sql.execute("SELECT discussion_channel, suggestion_channel FROM channel_assignments \
                        INNER JOIN servers on servers.id = channel_assignments.server and servers.id = ?", (server_dbid,))
    channel_config.append(res.fetchone())
    return channel_config
def command_params(command):
    params = command.split(" ")
    if len(params) >= 2:
        return params[1:]

def db_connect():
    global database
    db = sqlite3.connect(database, isolation_level=None)
    command = db.cursor()
    return command

def is_admin(user):
    return user.server_permissions.administrator

def can_kick(user):
    return True in [y.permissions.kick_members for y in user.roles] or is_admin(user)

def user_string(user):
    return user.name+"#"+user.discriminator

def build_embed(message, color):
    if color == "green":
        color = 0x36b319
    elif color == "red":
        color = 0xC70636
    elif color == "blue":
        color = 0xf6ecc
    embed = discord.Embed(colour=discord.Colour(color), description=message)
    return embed
    
client.loop.create_task(scheduled_tasks())
client.run(TOKEN)