"""License endpoints -- list and get license details."""

from fastapi import APIRouter, HTTPException

from app.config import settings

router = APIRouter(tags=["licenses"])

BASE = settings.BASE_URL

_LICENSES = {
    "mit": {
        "key": "mit",
        "name": "MIT License",
        "spdx_id": "MIT",
        "url": "https://api.github.com/licenses/mit",
        "node_id": "",
        "html_url": "http://choosealicense.com/licenses/mit/",
        "description": "A short and simple permissive license with conditions only requiring preservation of copyright and license notices.",
        "implementation": 'Add a file named "LICENSE" to your project with the license text.',
        "permissions": ["commercial-use", "modifications", "distribution", "private-use"],
        "conditions": ["include-copyright"],
        "limitations": ["no-liability"],
        "body": """MIT License

Copyright (c) [year] [fullname]

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
""",
        "featured": True,
    },
    "apache-2.0": {
        "key": "apache-2.0",
        "name": "Apache License 2.0",
        "spdx_id": "Apache-2.0",
        "url": "https://api.github.com/licenses/apache-2.0",
        "node_id": "",
        "html_url": "http://choosealicense.com/licenses/apache-2.0/",
        "description": "A permissive license whose main conditions require preservation of copyright and license notices.",
        "permissions": ["commercial-use", "modifications", "distribution", "patent-use", "private-use"],
        "conditions": ["include-copyright", "document-changes"],
        "limitations": ["trademark-use", "no-liability"],
        "body": "Apache License 2.0 text...",
        "featured": True,
    },
    "gpl-3.0": {
        "key": "gpl-3.0",
        "name": "GNU General Public License v3.0",
        "spdx_id": "GPL-3.0",
        "url": "https://api.github.com/licenses/gpl-3.0",
        "node_id": "",
        "html_url": "http://choosealicense.com/licenses/gpl-3.0/",
        "description": "Permissions of this strong copyleft license are conditioned on making available complete source code of licensed works.",
        "permissions": ["commercial-use", "modifications", "distribution", "patent-use", "private-use"],
        "conditions": ["include-copyright", "document-changes", "disclose-source", "same-license"],
        "limitations": ["no-liability"],
        "body": "GPL-3.0 license text...",
        "featured": True,
    },
    "bsd-2-clause": {
        "key": "bsd-2-clause",
        "name": 'BSD 2-Clause "Simplified" License',
        "spdx_id": "BSD-2-Clause",
        "url": "https://api.github.com/licenses/bsd-2-clause",
        "node_id": "",
        "html_url": "http://choosealicense.com/licenses/bsd-2-clause/",
        "description": "A permissive license that comes in two variants.",
        "body": "BSD 2-Clause license text...",
        "featured": False,
    },
    "unlicense": {
        "key": "unlicense",
        "name": "The Unlicense",
        "spdx_id": "Unlicense",
        "url": "https://api.github.com/licenses/unlicense",
        "node_id": "",
        "html_url": "http://choosealicense.com/licenses/unlicense/",
        "description": "A license with no conditions whatsoever.",
        "body": "Unlicense text...",
        "featured": True,
    },
}


@router.get("/licenses")
async def list_licenses():
    """List commonly used open source licenses."""
    return [
        {
            "key": lic["key"],
            "name": lic["name"],
            "spdx_id": lic.get("spdx_id", ""),
            "url": lic.get("url", ""),
            "node_id": lic.get("node_id", ""),
        }
        for lic in _LICENSES.values()
    ]


@router.get("/licenses/{key}")
async def get_license(key: str):
    """Get a license by key."""
    lic = _LICENSES.get(key.lower())
    if lic is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return lic
