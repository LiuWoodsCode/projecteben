Alright, **Pixel Prowler**, here’s a clean, no‑nonsense runbook on how to use the **`scp` (Secure Copy Protocol)** command.

***

## What `scp` Is

`scp` securely transfers files and directories between machines **over SSH**.  
If you can SSH into a host, you can usually SCP to/from it.

**Basic syntax**

```bash
scp [options] source destination
```

***

## Common Use Cases (90% of real life)

### 1. Copy a file **from local → remote**

```bash
scp file.txt user@remote_host:/path/to/destination/
```

✅ Example:

```bash
scp backup.sql admin@192.168.1.10:/var/backups/
```

***

### 2. Copy a file **from remote → local**

```bash
scp user@remote_host:/path/to/file.txt /local/path/
```

✅ Example:

```bash
scp admin@server.example.com:/var/log/syslog ./syslog
```

***

### 3. Copy a directory (recursive)

You *must* use `-r`.

```bash
scp -r myfolder user@remote_host:/path/to/destination/
```

✅ Example:

```bash
scp -r ./app admin@192.168.1.10:/opt/
```

***

### 4. Copy between two **remote hosts** (from your local machine)

```bash
scp user1@host1:/path/file user2@host2:/path/
```

✅ Requires SSH access to both.

***

## Useful Options (a.k.a. survival kit)

| Option | What it does                      |
| ------ | --------------------------------- |
| `-r`   | Copy directories recursively      |
| `-P`   | Specify **SSH port** (capital P)  |
| `-p`   | Preserve timestamps & permissions |
| `-C`   | Enable compression                |
| `-v`   | Verbose (debug mode)              |

***

### 5. SCP using a custom SSH port

```bash
scp -P 2222 file.txt user@remote_host:/path/
```

⚠️ Note: `scp -P`, not `-p`. Case matters.

***

### 6. Use an SSH key

```bash
scp -i ~/.ssh/id_rsa file.txt user@remote_host:/path/
```

Combine with port if needed:

```bash
scp -i ~/.ssh/id_rsa -P 2222 file.txt user@host:/path/
```

***

## Quoting paths (when spaces or wildcards exist)

```bash
scp "My File.txt" user@host:"/remote/path/My File.txt"
```

***

## Typical Errors & Fast Fixes

### ❌ `permission denied`

*   Wrong user
*   No write permissions on destination
*   SSH key not authorized

✅ Fix: Check user, permissions, or use `sudo` on the remote side (copy to `/tmp` first).

***

### ❌ SCP is slow

✅ Use compression:

```bash
scp -C largefile.iso user@remote:/path/
```

***

## Pro Tip: `scp` is stable, but…

For **large transfers** or **resumable copies**, prefer:

*   `rsync`
*   `sftp`

SCP is ideal for **quick, simple, secure copies**.

***

## Mental Model (memorize this)

    local → remote  = scp file user@host:/path
    remote → local  = scp user@host:/path/file .
    directory copy = scp -r folder user@host:/path

If you want, I can:

*   Convert this into a **cheat sheet**
*   Show SCP vs RSYNC
*   Walk through a **real troubleshooting scenario**

Just say the word.
