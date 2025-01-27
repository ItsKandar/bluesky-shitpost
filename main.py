# Main function to generate the post and post it
import sys
sys.stdout.reconfigure(encoding='utf-8')

from config import llm_token, prompt, bluesky_username, bluesky_password
import anthropic
from atproto import Client, client_utils

# Using claude.ia, this function generates a post for the blog.
def generate_text():
    client = anthropic.Anthropic(api_key=llm_token)
    message = client.messages.create(
        max_tokens=130,
        model="claude-3-5-sonnet-20241022",
        system=prompt,
        temperature=0.8,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Genere un shitpost de quelques phrase ou tu raconte une blague ou tu parles de politique et finis tes phrases avant 130 charactères"
                    }
                ]
            }
        ]
    )
    content = message.content
    text = " ".join(block.text for block in content if block.type == "text")
    print(text)
    return text

# generate_text()

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

postonblue(generate_text())