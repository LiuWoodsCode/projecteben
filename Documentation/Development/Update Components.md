> [!IMPORTANT]
> This is documentation for my future reference, this is reliant on my system and most likely will not work unless you have my exact same file structure and configuration.

Assuming you have my directory structure:
```bash
# For updating the local copy of the source tree
scp -r /Users/dapixelprowler/Documents/Code/projecteben/. \
  dapixelprowler@172.31.255.1:/home/dapixelprowler/projecteben/

# Update Ranboo server
scp -r /Users/dapixelprowler/Documents/Code/projecteben/Code/Ranboo/server/. \
  dapixelprowler@172.31.255.1:/home/dapixelprowler/server/
```

You can also use rsync:
```bash
# For updating the local copy of the source tree
rsync -avh --progress \
  /Users/dapixelprowler/Documents/Code/projecteben/ \
  dapixelprowler@172.31.255.1:/home/dapixelprowler/projecteben/

# Update Ranboo server
rsync -avh --progress \
  /Users/dapixelprowler/Documents/Code/projecteben/Code/Ranboo/server/ \
  dapixelprowler@172.31.255.1:/home/dapixelprowler/server/
```
