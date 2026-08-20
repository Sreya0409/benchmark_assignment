import os
import logging

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

logging.basicConfig(level=logging.DEBUG)

uri = os.getenv("AURA_URI")
user = os.getenv("AURA_USER")
password = os.getenv("AURA_PASSWORD")

print("URI:", uri)
print("User:", user)

driver = GraphDatabase.driver(
    uri,
    auth=(user, password),
    connection_timeout=15,
)

try:
    driver.verify_connectivity()
    print("✅ Neo4j Aura connected")
except Exception as e:
    print("❌ Neo4j Aura failed")
    print(type(e).__name__)
    print(e)
finally:
    driver.close()