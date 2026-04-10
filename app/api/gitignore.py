"""Gitignore template endpoints."""

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["gitignore"])

# A small set of common gitignore templates
_TEMPLATES = {
    "Python": """# Byte-compiled / optimized / DLL files
__pycache__/
*.py[cod]
*$py.class
*.so
dist/
build/
*.egg-info/
.eggs/
*.egg
.venv/
venv/
""",
    "Node": """node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
dist/
.env
""",
    "Java": """*.class
*.log
*.jar
*.war
*.nar
*.ear
*.zip
*.tar.gz
*.rar
target/
""",
    "Go": """*.exe
*.exe~
*.dll
*.so
*.dylib
*.test
*.out
vendor/
""",
    "C": """*.o
*.so
*.a
*.lib
*.exe
*.out
""",
    "C++": """*.o
*.so
*.a
*.lib
*.exe
*.out
*.d
build/
""",
    "Rust": """target/
Cargo.lock
**/*.rs.bk
""",
    "Ruby": """*.gem
*.rbc
.bundle/
vendor/bundle
log/
tmp/
""",
}


@router.get("/gitignore/templates")
async def list_templates():
    """List available gitignore templates."""
    return sorted(_TEMPLATES.keys())


@router.get("/gitignore/templates/{name}")
async def get_template(name: str):
    """Get a gitignore template by name."""
    template = _TEMPLATES.get(name)
    if template is None:
        # Case-insensitive fallback
        for key, value in _TEMPLATES.items():
            if key.lower() == name.lower():
                template = value
                name = key
                break
    if template is None:
        raise HTTPException(status_code=404, detail="Not Found")

    return {
        "name": name,
        "source": template,
    }
