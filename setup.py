from setuptools import setup

setup(
    name="x-feed-scraper",
    version="1.0.0",
    description="Read X/Twitter without API keys — Playwright-based timeline scraper",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="ghost-actual",
    url="https://github.com/ghost-actual/x-feed-scraper",
    py_modules=["x_feed"],
    python_requires=">=3.9",
    install_requires=[
        "playwright>=1.40",
    ],
    extras_require={
        "webhook": ["aiohttp>=3.9"],
        "all": ["aiohttp>=3.9", "httpx>=0.25"],
    },
    entry_points={
        "console_scripts": [
            "x-feed=x_feed:cli",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: Internet :: WWW/HTTP :: Dynamic Content",
    ],
)
