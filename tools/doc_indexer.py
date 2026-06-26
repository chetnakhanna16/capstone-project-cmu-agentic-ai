import re
from pathlib import Path
from dataclasses import dataclass
import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

JENKINS_ROOT = Path(__file__).parent.parent / "jenkins"
CHROMA_DIR = Path(__file__).parent.parent / "output" / "chroma_db"
COLLECTION_NAME = "jenkins_docs"


@dataclass
class Document:
    content: str
    source: str      # file path relative to jenkins root
    doc_type: str    # "markdown", "javadoc", "package_info", "pom"


def _chunk_text(text: str, max_chars: int = 1000, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks by logical sections first, then size."""
    # try to split on headings or blank lines first
    sections = re.split(r'\n#{1,3} |\n\n', text)
    chunks = []
    current = ""
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(current) + len(section) < max_chars:
            current += "\n\n" + section
        else:
            if current:
                chunks.append(current.strip())
            current = section
    if current:
        chunks.append(current.strip())
    return [c for c in chunks if len(c) > 50]


def _extract_javadoc(java_content: str, file_path: str) -> str:
    """Extract class-level Javadoc and class declaration from a Java file."""
    lines = java_content.splitlines()
    result = []
    in_comment = False
    class_found = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("/**"):
            in_comment = True
            result.append(line)
        elif in_comment:
            result.append(line)
            if "*/" in stripped:
                in_comment = False
        elif re.match(r'.*(public|abstract).*(class|interface|enum).*', stripped):
            result.append(line)
            class_found = True
            break

    if not result or not class_found:
        return ""
    return "\n".join(result)


def load_markdown_docs() -> list[Document]:
    docs = []
    for md_file in JENKINS_ROOT.rglob("*.md"):
        if any(p in md_file.parts for p in ("target", ".git", "node_modules")):
            continue
        content = md_file.read_text(errors="ignore").strip()
        if content:
            rel = str(md_file.relative_to(JENKINS_ROOT))
            docs.append(Document(content=content, source=rel, doc_type="markdown"))
    return docs


def load_package_info_docs() -> list[Document]:
    docs = []
    for pkg_file in JENKINS_ROOT.rglob("package-info.java"):
        if "target" in pkg_file.parts:
            continue
        content = pkg_file.read_text(errors="ignore").strip()
        if content:
            rel = str(pkg_file.relative_to(JENKINS_ROOT))
            docs.append(Document(content=content, source=rel, doc_type="package_info"))
    return docs


def load_extension_point_docs() -> list[Document]:
    """Extract Javadoc from classes annotated with @Extension or implementing ExtensionPoint."""
    docs = []
    markers = {"@Extension", "ExtensionPoint", "Describable", "@Plugin"}
    src_root = JENKINS_ROOT / "core" / "src" / "main" / "java"

    for java_file in src_root.rglob("*.java"):
        if "target" in java_file.parts:
            continue
        content = java_file.read_text(errors="ignore")
        if not any(m in content for m in markers):
            continue
        javadoc = _extract_javadoc(content, str(java_file))
        if javadoc:
            rel = str(java_file.relative_to(JENKINS_ROOT))
            # include the marker context so retrieval knows WHY this file matters
            header = f"[Extension Point File: {java_file.name}]\n"
            docs.append(Document(content=header + javadoc, source=rel, doc_type="javadoc"))
    return docs


def load_pom_docs() -> list[Document]:
    docs = []
    for pom in JENKINS_ROOT.rglob("pom.xml"):
        if "target" in pom.parts or ".git" in pom.parts:
            continue
        content = pom.read_text(errors="ignore")
        # extract just the dependencies section — rest is build config noise
        match = re.search(r'<dependencies>(.*?)</dependencies>', content, re.DOTALL)
        if match:
            rel = str(pom.relative_to(JENKINS_ROOT))
            docs.append(Document(content=f"[Dependencies: {rel}]\n{match.group(0)}", source=rel, doc_type="pom"))
    return docs


def build_index(force: bool = False) -> chromadb.Collection:
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    ef = DefaultEmbeddingFunction()

    existing = [c.name for c in client.list_collections()]
    if COLLECTION_NAME in existing:
        if not force:
            print(f"Index already exists ({COLLECTION_NAME}). Use force=True to rebuild.")
            return client.get_collection(COLLECTION_NAME, embedding_function=ef)
        client.delete_collection(COLLECTION_NAME)

    collection = client.create_collection(COLLECTION_NAME, embedding_function=ef)

    print("Loading documents...")
    all_docs = (
        load_markdown_docs()
        + load_package_info_docs()
        + load_extension_point_docs()
        + load_pom_docs()
    )
    print(f"  Loaded {len(all_docs)} documents")

    ids, texts, metadatas = [], [], []
    idx = 0
    for doc in all_docs:
        for chunk in _chunk_text(doc.content):
            ids.append(f"doc_{idx}")
            texts.append(chunk)
            metadatas.append({"source": doc.source, "doc_type": doc.doc_type})
            idx += 1

    print(f"  Total chunks to index: {idx}")

    # add in batches to avoid memory issues
    batch_size = 100
    for i in range(0, len(ids), batch_size):
        collection.add(
            ids=ids[i:i+batch_size],
            documents=texts[i:i+batch_size],
            metadatas=metadatas[i:i+batch_size],
        )
        print(f"  Indexed {min(i+batch_size, len(ids))}/{len(ids)} chunks", end="\r")

    print(f"\nIndex built: {len(ids)} chunks across {len(all_docs)} documents")
    return collection


def get_collection() -> chromadb.Collection:
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    ef = DefaultEmbeddingFunction()
    return client.get_collection(COLLECTION_NAME, embedding_function=ef)


def retrieve(query: str, n_results: int = 5) -> list[dict]:
    collection = get_collection()
    results = collection.query(query_texts=[query], n_results=n_results)
    output = []
    for i, doc in enumerate(results["documents"][0]):
        output.append({
            "content": doc,
            "source": results["metadatas"][0][i]["source"],
            "doc_type": results["metadatas"][0][i]["doc_type"],
        })
    return output


if __name__ == "__main__":
    build_index(force=True)
