from openai import OpenAI
from dotenv import load_dotenv
import os

# Load .env file
load_dotenv()

# Read API key from environment
api_key = os.getenv("OPENAI_API_KEY")

print("API Key Loaded:", api_key[:10] + "...")

# Create OpenAI client
client = OpenAI(api_key=api_key)

try:

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "user",
                "content": "hello"
            }
        ]
    )

    print("\nSUCCESS")
    print(response.choices[0].message.content)

except Exception as e:

    print("\nERROR")
    print(type(e))
    print(e)