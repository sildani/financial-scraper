# test_env.py
import warnings
# Silence SDK-level warnings from google-genai / pydantic-ai
warnings.filterwarnings("ignore", category=UserWarning)

import os
from dotenv import load_dotenv
from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel

load_dotenv()

model = GoogleModel('gemini-3.6-flash')
agent = Agent(model)

def main():
    result = agent.run_sync('Respond with: Environment setup is working!')
    print(f"\n[Response]: {result.output}\n")

if __name__ == "__main__":
    main()