"""Setup script for Eco-Loop Building Agents package."""

from setuptools import setup, find_packages
from pathlib import Path

# Read README for long description
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

# Read requirements
requirements_file = Path(__file__).parent / "requirements.txt"
requirements = []
if requirements_file.exists():
    requirements = [
        line.strip()
        for line in requirements_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

setup(
    name="eco-loop-building-agents",
    version="0.1.0",
    description="AI-driven building energy optimization with EnergyPlus and LLM control",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="SynapseEnergy Team",
    author_email="contact@synapseenergy.example.com",
    url="https://github.com/synapseenergy/eco-loop-building-agents",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.9",
    install_requires=requirements,
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-cov>=4.1.0",
            "black>=23.7.0",
            "isort>=5.12.0",
            "mypy>=1.5.0",
            "flake8>=6.1.0",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Home Automation",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    keywords="building energy hvac ai llm energyplus mcp optimization",
    project_urls={
        "Bug Reports": "https://github.com/synapseenergy/eco-loop-building-agents/issues",
        "Source": "https://github.com/synapseenergy/eco-loop-building-agents",
    },
)
