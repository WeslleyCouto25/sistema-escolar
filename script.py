from dotenv import load_dotenv
import os  # ← ESTAVA FALTANDO ESTA LINHA!
load_dotenv()

print("="*50)
print("TESTE DE CARREGAMENTO DO .env")
print(f"OPENAI_API_KEY: {os.getenv('OPENAI_API_KEY')}")
print(f"Tamanho da chave: {len(os.getenv('OPENAI_API_KEY') or '')}")
print("="*50)