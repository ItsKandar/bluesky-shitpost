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
import re
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
def _demander(consigne, derniers_posts=None, max_tokens=300):
    if derniers_posts:
        consigne += "\n\nTes derniers posts, pour ne pas refaire les mêmes blagues (ce ne sont pas des modèles à imiter) :\n" + \
            "\n".join(f"- {p}" for p in derniers_posts[-10:])
    client = anthropic.Anthropic(api_key=llm_token)
    message = client.messages.create(
        max_tokens=max_tokens,
        model="claude-haiku-4-5",
        system=system,
        # SDK anthropic 1.x : temperature n'est plus un argument nommé ; Haiku 4.5 l'accepte encore
        extra_body={"temperature": 0.8},
        messages=[{"role": "user", "content": [{"type": "text", "text": consigne}]}]
    )
    text = " ".join(block.text for block in message.content if block.type == "text").strip()
    # Guillemets autour du post entier seulement (ne pas couper une citation au début)
    if len(text) > 1 and text[0] in '"«' and text[-1] in '"»':
        text = text[1:-1].strip()
    return text


def _court(text):
    return 0 < len(text) <= LIMITE_BLUESKY


# Le post est précédé d'une courte analyse politique (non publiée) : elle donne un
# angle au shitpost, et elle est recopiée dans le salon de suivi.
CONSIGNE_ANALYSE = (
    "Réponds avec trois champs.\n"
    "- analyse : 2 ou 3 phrases, ta lecture politique du sujet depuis ta position (marxiste, "
    "anticapitaliste, debordienne, féministe, queer). À qui ça profite, quel rapport de classe "
    "ou de force ça révèle, ce que le discours dominant ou le spectacle cache.\n"
    "- post : un shitpost de 280 caractères maximum qui fait passer CETTE analyse : la blague sert "
    "l'argument, le lecteur doit comprendre ta critique. Ton brainrot habituel, second degré. "
    "N'invente ni faits, ni chiffres, ni citations ; ne prête pas de fausses répliques à des "
    "personnes réelles ; pas de lien, pas de hashtag.\n"
    "- passe : true seulement si le sujet est tragique, violent ou touche des victimes "
    "(analyse et post vides dans ce cas), sinon false."
)

# Réponse en JSON contraint par l'API (structured outputs) : analyse et post toujours séparés
SCHEMA_POST = {
    "type": "object",
    "properties": {
        "analyse": {"type": "string"},
        "post": {"type": "string"},
        "passe": {"type": "boolean"},
    },
    "required": ["analyse", "post", "passe"],
    "additionalProperties": False,
}


# Format tiré au hasard pour chaque post, toujours différent de celui du post précédent
# (sans ça, le modèle enchaîne les faux dialogues en imitant ses derniers posts).
FORMATS = {
    "phrase": ("une ou deux phrases cinglantes, sans dialogue", 3),
    "moi_quand": ("un « moi quand… » ou un « POV : … »", 1),
    "annonce": ("un faux communiqué ou une fausse annonce officielle", 1),
    "question": ("une question rhétorique qui retourne le discours dominant", 1),
    "liste": ("une courte liste ou un faux classement", 1),
    "dialogue": ("un faux dialogue entre institutions ou groupes sociaux (jamais entre personnes réelles nommées)", 0.6),
}


def choisir_format(dernier=None):
    noms = [n for n in FORMATS if n != dernier]
    return random.choices(noms, weights=[FORMATS[n][1] for n in noms])[0]


def consigne_format(nom, dernier=None):
    texte = f"\nFormat imposé pour le post : {FORMATS[nom][0]}."
    if dernier in FORMATS:
        texte += (f" Ton dernier post était {FORMATS[dernier][0]} : surtout ne reprends ni ce format "
                  "ni sa structure.")
    return texte


def _demander_post(consigne, derniers_posts=None):
    if derniers_posts:
        consigne += "\n\nTes derniers posts, pour ne pas refaire les mêmes blagues (ce ne sont pas des modèles à imiter) :\n" + \
            "\n".join(f"- {p}" for p in derniers_posts[-10:])
    client = anthropic.Anthropic(api_key=llm_token)
    message = client.messages.create(
        max_tokens=1000,
        model="claude-haiku-4-5",
        system=system,
        extra_body={"temperature": 0.8},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_POST}},
        messages=[{"role": "user", "content": [{"type": "text", "text": consigne}]}]
    )
    if message.stop_reason != "end_turn":
        raise ValueError(f"réponse incomplète ({message.stop_reason})")
    texte = next(b.text for b in message.content if b.type == "text")
    return json.loads(texte)


def _post_avec_analyse(sujet, derniers_posts=None, passe_possible=False, format_post=None, dernier_format=None):
    """Renvoie (post, analyse), ou (None, None) si le sujet ne s'y prête pas ou après 3 échecs."""
    format_post = format_post or choisir_format(dernier_format)
    consigne = sujet + "\n\n" + CONSIGNE_ANALYSE + consigne_format(format_post, dernier_format)
    for _ in range(3):
        try:
            r = _demander_post(consigne, derniers_posts)
        except (ValueError, StopIteration) as e:
            print(f"[génération] réponse inutilisable : {e}")
            continue
        if r["passe"] and passe_possible:
            return None, None
        post, analyse = r["post"].strip(), r["analyse"].strip()
        if len(post) > 1 and post[0] in '"«' and post[-1] in '"»':
            post = post[1:-1].strip()
        if _court(post):
            print(f"[analyse] {analyse}\n{post}")
            return post, analyse
    return None, None


# Using claude.ia, this function generates a post for the blog.
def generate_text(prompt=None, derniers_posts=None, avec_analyse=False, format_post=None, dernier_format=None):
    if prompt is None:
        prompt = selectprompt()
    post, analyse = _post_avec_analyse(f"Sujet : tu {prompt}.", derniers_posts,
                                       format_post=format_post, dernier_format=dernier_format)
    return (post, analyse) if avec_analyse else post


def generate_actu(article, derniers_posts=None, format_post=None, dernier_format=None):
    """(post, analyse) sur un article, ou (None, None) si le sujet ne s'y prête pas."""
    sujet = (
        "Voici un titre d'actualité du jour, réagis-y :\n"
        f"Titre : {article['titre']}\n"
        f"Résumé : {article['resume']}"
    )
    post, analyse = _post_avec_analyse(sujet, derniers_posts, passe_possible=True,
                                       format_post=format_post, dernier_format=dernier_format)
    return post, analyse


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
    """Choisit le sujet (actualité ou liste fixe), génère (texte, article, analyse)."""
    derniers = etat["derniers_posts"]
    dernier_format = etat.get("dernier_format")
    format_post = choisir_format(dernier_format)
    etat["dernier_format"] = format_post
    if random.random() < PART_ACTU:
        articles = actu.articles_recents(set(etat["liens_vus"]))
        for article in articles[:4]:
            etat["liens_vus"].append(article["lien"])
            text, analyse = generate_actu(article, derniers, format_post, dernier_format)
            if text:
                return text, article, analyse
        print("[auto] aucune actualité exploitable, repli sur un sujet fixe")
    text, analyse = generate_text(derniers_posts=derniers, avec_analyse=True,
                                  format_post=format_post, dernier_format=dernier_format)
    if not text:
        raise RuntimeError("aucun post utilisable généré")
    return text, None, analyse


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


async def publier(text, article=None, origine="auto", analyse=None):
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
        if analyse:
            source += f"\n-# Analyse : {analyse[:600]}"
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
        text, article, analyse = await asyncio.to_thread(preparer_post_auto, etat)
        sauver_etat(etat)  # liens d'articles marqués comme vus
        await publier(text, article, analyse=analyse)
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
    if not text:
        await interaction.followup.send("Génération impossible : aucun post utilisable, réessaie.")
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
