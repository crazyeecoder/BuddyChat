import os
import fitz  # PyMuPDF
from sentence_transformers import SentenceTransformer
from supabase import create_client
from dotenv import load_dotenv
import uuid

load_dotenv()

# --- Init ---
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
model = SentenceTransformer("all-MiniLM-L6-v2")

CHUNK_SIZE = 500  # words per chunk

def extract_text_pdf(path):
    doc = fitz.open(path)
    full_text = ""
    for page in doc:
        full_text += page.get_text()
    return full_text

def extract_text_md(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def extract_text(path):
    if path.endswith(".md"):
        return extract_text_md(path)
    return extract_text_pdf(path)

def chunk_text(text, chunk_size=CHUNK_SIZE):
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
    return chunks

def embed_and_store(chunks, source_name):
    print(f"Embedding {len(chunks)} chunks from {source_name}...")
    for i, chunk in enumerate(chunks):
        embedding = model.encode(chunk).tolist()
        supabase.table("knowledge_base").insert({
            "id": str(uuid.uuid4()),
            "chunk_text": chunk,
            "embedding": embedding,
            "source": source_name
        }).execute()
        print(f"  Stored chunk {i+1}/{len(chunks)}")

def ingest_file(file_path):
    source_name = os.path.basename(file_path)
    print(f"\nProcessing: {source_name}")
    text = extract_text(file_path)
    chunks = chunk_text(text)
    embed_and_store(chunks, source_name)
    print(f"Done: {source_name}")

if __name__ == "__main__":
    papers_dir = "papers"

    if not os.path.exists(papers_dir):
        os.makedirs(papers_dir)
        print("Created 'papers' folder. Add your PDFs or .md files there and run again.")
    else:
        files = [
            f for f in os.listdir(papers_dir)
            if f.endswith(".pdf") or f.endswith(".md")
        ]
        if not files:
            print("No PDFs or .md files found in 'papers' folder. Add some and run again.")
        else:
            for file in files:
                ingest_file(os.path.join(papers_dir, file))
            print("\nAll files ingested into knowledge_base!")