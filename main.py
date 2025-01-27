# Main function to generate the post and post it
from config import llm_token, system, bluesky_username, bluesky_password, discord_token
import anthropic
from atproto import Client, client_utils
import random
import discord
from discord import app_commands, ui, Button
from discord.ext import commands
import requests
import sys
import asyncio
sys.stdout.reconfigure(encoding='utf-8')

def selectprompt():
    promptlist = ['raconte une blague ou tu parles de politique', 'parles de ton amour pour poutou', 'parles de guy debord', 'insulte marguerite stern', 'raconte une blague sur les marxistes']
    prompt = promptlist[random.randint(0, len(promptlist)-1)]
    return prompt

# Using claude.ia, this function generates a post for the blog.
def generate_text(prompt=None):
    if prompt is None:
        prompt = selectprompt()
    client = anthropic.Anthropic(api_key=llm_token)
    message = client.messages.create(
        max_tokens=130,
        model="claude-3-5-sonnet-20241022",
        system=system,
        temperature=0.8,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Genere un shitpost de max 130 characteres quelques phrase ou tu " + prompt
                    }
                ]
            }
        ]
    )
    content = message.content
    text = " ".join(block.text for block in content if block.type == "text")
    print(text)
    return text

# Function to post on bluesky
def postonblue(text):
    client = Client()
    profile = client.login(bluesky_username, bluesky_password)
    print('Profile:', profile.display_name)
    
    # Post the message
    post = client.send_post(text=text)
    client.like(post.uri, post.cid)
    print('Posted:', post.uri)
    return post

def generateanswer(prompt):
    client = anthropic.Anthropic(api_key=llm_token)
    message = client.messages.create(
        max_tokens=130,
        model="claude-3-5-sonnet-20241022",
        system=system,
        temperature=0.8,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text", "text": "Genere une reponse shitpost de 130 characteres max au bluesky suivant: " + prompt
                    }
                ]
            }
        ]
    )
    content = message.content
    text = " ".join(block.text for block in content if block.type == "text")
    print(text)
    return text

# generateanswer(posttoanswer)
# generate_text()

# Initialize discord bot
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

async def get_bot():
    return bot


# Confirme la connexion
@bot.event
async def on_ready():
    print('Logged in as', bot.user)
    try:
        synced = await bot.tree.sync()
        print(f"Synced {synced} commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    await bot.change_presence(activity=discord.Game(name='UwU'))
    
# Commande pour générer un post
class ShitpostView(discord.ui.View):
    def __init__(self, text):
        super().__init__()
        self.text = text

    @discord.ui.button(label='Oui', style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        postonblue(self.text)
        await asyncio.sleep(5)
        await interaction.response.send_message('Message posté sur bluesky!')
        self.stop()

    @discord.ui.button(label='Non', style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message('Message non posté')
        self.stop()

@bot.tree.command(name='shitpost', description='Génère un shitpost avec un prompt et propose de le poster sur bluesky')
async def shitpost(interaction: discord.Interaction, user_prompt: str):
    text = generate_text(user_prompt)
    await interaction.response.send_message(text)
    # Wait a moment to ensure the first message is sent
    await asyncio.sleep(5)
    view = ShitpostView(text)
    await interaction.followup.send('Voulez-vous poster ce message sur bluesky?', view=view)
        
# Commande d'arret du bot
@bot.tree.command(name='stop', description='Arrête le bot')
@commands.is_owner()
async def stop(interaction: discord.Interaction):
    await interaction.response.send_message('Arrêt du bot...')
    await bot.close()
    
bot.run(discord_token)