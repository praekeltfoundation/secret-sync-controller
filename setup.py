import os

from setuptools import find_packages, setup

HERE = os.path.abspath(os.path.dirname(__file__))


def read(*parts):
    with open(os.path.join(HERE, *parts)) as f:
        return f.read()


setup(
    name="secret-sync-controller",
    version="0.0.1dev0",
    license="MIT",
    description="Secret Sync Controller",
    author="Praekelt.org SRE team",
    author_email="sre@praekelt.org",
    long_description=read("README.md"),
    long_description_content_type="text/markdown",
    packages=find_packages("src"),
    package_dir={"": "src"},
    python_requires=">=3.7",
    install_requires=[
        "attrs",
        # Pin kopf because it is rapidly evolving.
        "kopf==0.24",
        "pykube-ng>=19.10.0",
    ],
    extras_require={
        "dev": [
            "black",
            "flake8",
            "isort",
            "pytest>=4.0.0",
            "pytest-cov",
            "pytest-responses",
            "pyyaml",
            "responses",
        ],
    },
)
