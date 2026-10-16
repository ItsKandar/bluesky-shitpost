# Main function to generate the post and post it
import config
from config import llm_token, system, bluesky_username, bluesky_password, discord_token
import anthropic
from atproto import Client
import random
import discord
from discord import Button
from discord.ext import commands, tasks
import asyncio
import json
import os
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import actu

sys.stdout.reconfigure(encoding='utf-8')

# --- Publication automatique ---------------------------------------------
# Entre POSTS_MIN et POSTS_MAX posts par jour, à des heures tirées au hasard
# entre HEURE_DEBUT et HEURE_FIN. PART_ACTU des posts réagissent à l'actualité.
# Chaque post publié est recopié dans le salon SALON_SUIVI avec un bouton
# pour le supprimer de Bluesky.
FUSEAU = ZoneInfo("Europe/Paris")
POSTS_MIN, POSTS_MAX = 3, 5
HEURE_DEBUT, HEURE_FIN = time(9, 0), time(23, 0)
PART_ACTU = 0.75
SALON_SUIVI = getattr(config, "salon_suivi", 1333446495340462112)
AUTO = getattr(config, "publication_auto", True)
FICHIER_ETAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "etat.json")
LIMITE_BLUESKY = 300  # caractères

ID_SUPPRIMER = "marguerite:supprimer:"


def selectprompt():
    promptlist = ['raconte une blague ou tu parles de politique', 'parles de ton amour pour poutou', 'parles de guy debord', 'raconte une blague sur les marxistes']
    prompt = promptlist[random.randint(0, len(promptlist)-1)]
    return prompt


# --- État persistant (plan du jour, derniers posts, articles déjà traités) ---
def charger_etat():
    try:
        with open(FICHIER_ETAT, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"jour": None, "plan": [], "faits": [], "derniers_posts": [], "liens_vus": []}


def sauver_etat(etat):
    os.makedirs(os.path.dirname(FICHIER_ETAT), exist_ok=True)
    tmp = FICHIER_ETAT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FICHIER_ETAT)


def planifier(jour):
    """Heures de publication du jour (HH:MM, heure de Paris), triées."""
    debut = datetime.combine(jour, HEURE_DEBUT)
    duree = (datetime.combine(jour, HEURE_FIN) - debut).total_seconds()
    n = random.randint(POSTS_MIN, POSTS_MAX)
    heures = sorted(debut + timedelta(seconds=random.uniform(0, duree)) for _ in range(n))
    return [h.strftime("%H:%M") for h in heures]


# --- Génération --------------------------------------------------------------
def _demander(consigne, derniers_posts=None):
    if derniers_posts:
        consigne += "\n\nTes derniers posts (ne te répète pas, change de blague et de tournure) :\n" + \
            "\n".join(f"- {p}" for p in derniers_posts[-10:])
    client = anthropic.Anthropic(api_key=llm_token)
    message = client.messages.create(
        max_tokens=300,
        model="claude-haiku-4-5",
        system=system,
        # SDK anthropic 1.x : temperature n'est plus un argument nommé ; Haiku 4.5 l'accepte encore
        extra_body={"temperature": 0.8},
        messages=[{"role": "user", "content": [{"type": "text", "text": consigne}]}]
    )
    text = " ".join(block.text for block in message.content if block.type == "text").strip()
    return text.strip('"« »').strip()


def _court(text):
    return 0 < len(text) <= LIMITE_BLUESKY


# Using claude.ia, this function generates a post for the blog.
def generate_text(prompt=None, derniers_posts=None):
    if prompt is None:
        prompt = selectprompt()
    consigne = f"Genere un shitpost de 280 caracteres maximum, quelques phrases ou tu {prompt}"
    for _ in range(3):
        text = _demander(consigne, derniers_posts)
        if _court(text):
            break
    print(text)
    return text


def generate_actu(article, derniers_posts=None):
    """Shitpost sur un article. Renvoie None si le sujet ne s'y prête pas."""
    consigne = (
        "Voici un titre d'actualité du jour :\n"
        f"Titre : {article['titre']}\n"
        f"Résumé : {article['resume']}\n\n"
        "Réagis-y avec un shitpost de 280 caracteres maximum, dans ton style. "
        "Moque-toi de l'événement tel que décrit, sans inventer de faits, de chiffres ni de citations, "
        "et sans attribuer à une personne réelle des propos ou des actes qui ne sont pas dans le titre. "
        "Pas de lien, pas de hashtag. Si le sujet est tragique, violent ou touche des victimes, "
        "réponds uniquement PASSE."
    )
    for _ in range(3):
        text = _demander(consigne, derniers_posts)
        if text.upper().startswith("PASSE"):
            return None
        if _court(text):
            print(text)
            return text
    return None


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


def lien_post(uri):
    return f"https://bsky.app/profile/{bluesky_username}/post/{uri.rsplit('/', 1)[-1]}"


def supprimer_post(rkey):
    client = Client()
    client.login(bluesky_username, bluesky_password)
    me = client.me.did
    client.delete_post(f"at://{me}/app.bsky.feed.post/{rkey}")


def generateanswer(prompt):
    client = anthropic.Anthropic(api_key=llm_token)
    message = client.messages.create(
        max_tokens=130,
        model="claude-haiku-4-5",
        system=system,
        # SDK anthropic 1.x : temperature n'est plus un argument nommé ; Haiku 4.5 l'accepte encore
        extra_body={"temperature": 0.8},
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


def preparer_post_auto(etat):
    """Choisit le sujet (actualité ou liste fixe) et génère le texte."""
    derniers = etat["derniers_posts"]
    if random.random() < PART_ACTU:
        articles = actu.articles_recents(set(etat["liens_vus"]))
        for article in articles[:4]:
            etat["liens_vus"].append(article["lien"])
            text = generate_actu(article, derniers)
            if text:
                return text, article
        print("[auto] aucune actualité exploitable, repli sur un sujet fixe")
    return generate_text(derniers_posts=derniers), None


# Initialize discord bot
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

async def get_bot():
    return bot


class SuppressionView(discord.ui.View):
    """Bouton « Supprimer de Bluesky » ; traité par on_interaction (survit aux redémarrages)."""
    def __init__(self, rkey):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label='Supprimer de Bluesky', style=discord.ButtonStyle.danger,
                                        custom_id=ID_SUPPRIMER + rkey))


async def publier(text, article=None, origine="auto"):
    """Publie sur Bluesky, mémorise le post et le recopie dans le salon de suivi."""
    post = await asyncio.to_thread(postonblue, text)
    etat = charger_etat()
    etat["derniers_posts"] = (etat["derniers_posts"] + [text])[-15:]
    etat["liens_vus"] = etat["liens_vus"][-300:]
    sauver_etat(etat)
    try:
        salon = bot.get_channel(SALON_SUIVI) or await bot.fetch_channel(SALON_SUIVI)
    except discord.DiscordException as e:
        print(f"[auto] salon de suivi {SALON_SUIVI} inaccessible : {e}")
        salon = None
    if salon:
        source = f"\n-# Actu : [{article['titre']}](<{article['lien']}>) ({article['source']})" if article else ""
        await salon.send(f"**Post {origine}** · {lien_post(post.uri)}\n> {text}{source}",
                         view=SuppressionView(post.uri.rsplit('/', 1)[-1]))
    return post


@tasks.loop(minutes=1)
async def publication_auto():
    maintenant = datetime.now(FUSEAU)
    jour = maintenant.date().isoformat()
    etat = charger_etat()
    if etat.get("jour") != jour:
        plan = planifier(maintenant.date())
        # Créneaux déjà passés au moment de planifier (démarrage en cours de journée) : ignorés
        etat.update(jour=jour, plan=plan, faits=[h for h in plan if h < maintenant.strftime("%H:%M")])
        sauver_etat(etat)
        print(f"[auto] plan du {jour} : {', '.join(etat['plan'])}")
    heure = maintenant.strftime("%H:%M")
    a_faire = [h for h in etat["plan"] if h <= heure and h not in etat["faits"]]
    if not a_faire:
        return
    # Créneaux manqués (bot arrêté) : on n'en rattrape qu'un seul
    etat["faits"].extend(a_faire)
    sauver_etat(etat)
    try:
        text, article = await asyncio.to_thread(preparer_post_auto, etat)
        sauver_etat(etat)  # liens d'articles marqués comme vus
        await publier(text, article)
    except Exception as e:
        print(f"[auto] échec de la publication de {a_faire[-1]} : {e}")


@publication_auto.before_loop
async def attendre_connexion():
    await bot.wait_until_ready()


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
    if AUTO and not publication_auto.is_running():
        publication_auto.start()


@bot.event
async def on_interaction(interaction: discord.Interaction):
    cid = (interaction.data or {}).get("custom_id", "")
    if interaction.type != discord.InteractionType.component or not cid.startswith(ID_SUPPRIMER):
        return
    await interaction.response.defer()
    try:
        await asyncio.to_thread(supprimer_post, cid[len(ID_SUPPRIMER):])
        await interaction.message.edit(
            content=f"🗑️ **Supprimé de Bluesky** par {interaction.user.mention}\n{interaction.message.content}", view=None)
    except Exception as e:
        await interaction.followup.send(f"Suppression impossible : {e}", ephemeral=True)

# Commande pour générer un post
class ShitpostView(discord.ui.View):
    def __init__(self, text):
        super().__init__()
        self.text = text

    @discord.ui.button(label='Oui', style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        await publier(self.text, origine="manuel")
        await interaction.followup.send('Message posté sur bluesky!')
        self.stop()

    @discord.ui.button(label='Non', style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message('Message non posté')
        self.stop()

@bot.tree.command(name='shitpost', description='Génère un shitpost avec un prompt et propose de le poster sur bluesky')
async def shitpost(interaction: discord.Interaction, user_prompt: str):
    await interaction.response.defer()
    try:
        text = await asyncio.to_thread(generate_text, user_prompt)
    except anthropic.APIError as e:
        await interaction.followup.send(f"Génération impossible : {getattr(e, 'message', e)}")
        return
    await interaction.followup.send(text)
    view = ShitpostView(text)
    await interaction.followup.send('Voulez-vous poster ce message sur bluesky?', view=view)

@bot.tree.command(name='answer', description='Génère une réponse à un post bluesky')
async def answer(interaction: discord.Interaction, posttoanswer: str):
    await interaction.response.defer()
    try:
        text = await asyncio.to_thread(generateanswer, posttoanswer)
    except anthropic.APIError as e:
        await interaction.followup.send(f"Génération impossible : {getattr(e, 'message', e)}")
        return
    await interaction.followup.send(text)

@bot.tree.command(name='planning', description='Heures des posts automatiques du jour')
async def planning(interaction: discord.Interaction):
    etat = charger_etat()
    if not AUTO or not etat.get("plan"):
        await interaction.response.send_message("Pas de publication automatique prévue.")
        return
    lignes = [f"{'✅' if h in etat['faits'] else '⏳'} {h}" for h in etat["plan"]]
    await interaction.response.send_message(f"Posts automatiques du {etat['jour']} :\n" + "\n".join(lignes))

# Commande d'arret du bot
@bot.tree.command(name='stop', description='Arrête le bot')
@commands.is_owner()
async def stop(interaction: discord.Interaction):
    await interaction.response.send_message('Arrêt du bot...')
    await bot.close()

if __name__ == "__main__":
    bot.run(discord_token)
