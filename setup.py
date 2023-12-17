from setuptools import setup, find_packages

setup(
    name='wayback',
    version='0.0.1',
    author='Ronald E. Robertson',
    author_email='rer@acm.org',
    packages=find_packages(),
    description='A wrapper for collecting data from the Wayback Machine',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    install_requires=[
        'waybackpy',
        'requests',
        'pandas',
        'tqdm',
    ],
)