"""
Knowledge Base — ChromaDB-backed RAG for triathlon coaching principles.
Loads markdown documents from knowledge/ directory, embeds them, and provides
a query() method that returns relevant coaching chunks.
"""
import os
from pathlib import Path

try:
    import chromadb
    from chromadb.utils import embedding_functions
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    print("Warning: chromadb not installed. RAG will use fallback keyword search.")


class KnowledgeBase:
    _instance = None
    
    @classmethod
    def get_instance(cls, knowledge_dir="knowledge", db_dir=None):
        """Return the singleton KnowledgeBase instance. Creates it on first call."""
        if cls._instance is None:
            cls._instance = cls(knowledge_dir=knowledge_dir, db_dir=db_dir)
        return cls._instance
    
    def __init__(self, knowledge_dir="knowledge", db_dir=None):
        self.knowledge_dir = Path(knowledge_dir)
        if db_dir is None:
            # Use project-local directory
            self.db_dir = str(Path(__file__).parent.parent.parent / "chroma_db")
        else:
            self.db_dir = db_dir
        self.documents = []
        self.chunk_metadata = []
        
        # Load all documents into memory (always available)
        self._load_documents()
        
        # Try to initialize ChromaDB if available
        self.collection = None
        if CHROMADB_AVAILABLE:
            self._init_chromadb()
    
    def _load_documents(self):
        """Load all markdown files from the knowledge directory."""
        if not self.knowledge_dir.exists():
            print(f"Warning: {self.knowledge_dir} not found.")
            return
        
        for md_file in sorted(self.knowledge_dir.glob("*.md")):
            with open(md_file, 'r') as f:
                content = f.read()
            
            # Split into chunks by section headers
            chunks = self._split_into_chunks(content, str(md_file.name))
            self.documents.extend(chunks)
        
        print(f"Loaded {len(self.documents)} knowledge chunks from {self.knowledge_dir}")
    
    def _split_into_chunks(self, content, source_file, max_chunk_size=500):
        """Split a markdown document into chunks by ## headers."""
        chunks = []
        current_chunk = ""
        current_header = source_file
        
        for line in content.split("\n"):
            if line.startswith("## "):
                # Save previous chunk if it has content
                if current_chunk.strip():
                    chunks.append({
                        "text": current_chunk.strip(),
                        "source": source_file,
                        "section": current_header
                    })
                current_header = line.replace("## ", "").strip()
                current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"
                
                # If chunk is getting too large, split it
                if len(current_chunk) > max_chunk_size and line.startswith("- "):
                    chunks.append({
                        "text": current_chunk.strip(),
                        "source": source_file,
                        "section": current_header
                    })
                    current_chunk = ""
        
        # Don't forget the last chunk
        if current_chunk.strip():
            chunks.append({
                "text": current_chunk.strip(),
                "source": source_file,
                "section": current_header
            })
        
        return chunks
    
    def _init_chromadb(self):
        """Initialize ChromaDB with embedded documents."""
        try:
            client = chromadb.PersistentClient(path=self.db_dir)
            
            # Use default embedding function (all-MiniLM-L6-v2)
            ef = embedding_functions.DefaultEmbeddingFunction()
            
            # Get or create collection
            self.collection = client.get_or_create_collection(
                name="coaching_knowledge",
                embedding_function=ef
            )
            
            # Only add documents if collection is empty
            if self.collection.count() == 0 and self.documents:
                texts = [d["text"] for d in self.documents]
                metadatas = [{"source": d["source"], "section": d["section"]} for d in self.documents]
                ids = [f"chunk_{i}" for i in range(len(texts))]
                
                self.collection.add(
                    documents=texts,
                    metadatas=metadatas,
                    ids=ids
                )
                print(f"Indexed {len(texts)} chunks into ChromaDB.")
            else:
                print(f"ChromaDB collection already has {self.collection.count()} chunks.")
                
        except Exception as e:
            print(f"ChromaDB initialization failed: {e}. Using fallback search.")
            self.collection = None
    
    def query(self, text, n_results=3):
        """
        Query the knowledge base for relevant coaching principles.
        Returns a list of text chunks most relevant to the query.
        """
        if self.collection:
            # ChromaDB vector search
            results = self.collection.query(
                query_texts=[text],
                n_results=n_results
            )
            return results["documents"][0] if results["documents"] else []
        else:
            # Fallback: simple keyword matching
            return self._keyword_search(text, n_results)
    
    def _keyword_search(self, text, n_results=3):
        """Fallback search using keyword matching when ChromaDB is unavailable."""
        keywords = text.lower().split()
        scored = []
        
        for doc in self.documents:
            doc_text = doc["text"].lower()
            score = sum(1 for kw in keywords if kw in doc_text)
            if score > 0:
                scored.append((score, doc["text"]))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        return [text for _, text in scored[:n_results]]
    
    def rebuild(self):
        """Force rebuild of the ChromaDB index."""
        if CHROMADB_AVAILABLE:
            import shutil
            if os.path.exists(self.db_dir):
                shutil.rmtree(self.db_dir)
            self.documents = []
            self.chunk_metadata = []
            self._load_documents()
            self._init_chromadb()


if __name__ == "__main__":
    kb = KnowledgeBase()
    results = kb.query("athlete HRV dropping, what should I do?")
    print("\n--- Query Results ---")
    for i, r in enumerate(results):
        print(f"\n[{i+1}] {r[:200]}...")
