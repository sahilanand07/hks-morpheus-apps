# hks-node-prep: proxy and time for a new HKS cluster

Prepares four fresh Ubuntu 24.04 VMs (**1 master + 3 workers**) for an HKS Hybrid cluster build in Morpheus. It handles the two things that broke the first lab build: proxy settings and clock drift. You run one command on vmemgr; it prepares all four VMs in parallel and prints a READY or NOT READY summary.

| File | What it does |
| --- | --- |
| `node.env` | **The only file you edit.** Node IPs, SSH user, proxy, networks, NTP, timezone. |
| `prep-nodes.sh` | The runner. Runs on vmemgr, copies the scripts to every node over SSH, runs them, and summarises the results. |
| `prep-proxy.sh` | Runs on a node. Removes old or competing proxy settings, writes one clean set, then tests it. |
| `prep-time.sh` | Runs on a node. Points timesyncd (or chrony) at a lab NTP server, sets the timezone, waits for sync, and compares the clock with the appliance. |

## 1. What each VM needs

* Ubuntu 24.04.
* A **unique hostname**.
* A **fixed IP** on `eth0`.
* An **empty** 50–100 GB second disk at `/dev/vdb`. In Morpheus you can add it with Actions → Reconfigure.
* At least 2 vCPU and 4 GB RAM. Give the master more, such as 4 vCPU and 8 GB.
* An SSH user that vmemgr can log in to with a key, with **passwordless sudo**.
* VMs cloned from one template must not share `/etc/machine-id` or the VM UUID. `check` finds this.

## 2. Run it

```bash
# on vmemgr, in this folder
vi node.env                      # NODES (master first), SSH_USER, NTP_SERVERS if you know one, TIMEZONE

for n in 192.168.200.61 192.168.200.62 192.168.200.63 192.168.200.64; do ssh-copy-id <user>@$n; done   # once
# on each node, once, if sudo asks for a password:
#   echo '<user> ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/hks-prep

bash prep-nodes.sh all           # proxy, then time, then the read-only checks
```

Wait until the summary ends with **`READY - no FAIL on any node`**:

```
NODE             ROLE     HOSTNAME             RESULTS
192.168.200.61   master   hks-m1               prep-proxy OK  |  prep-time OK  |  preflight OK
192.168.200.62   worker   hks-w1               prep-proxy OK  |  prep-time OK  |  preflight OK
...
READY - no FAIL on any node. Next: snapshot the VMs, then build the cluster in Morpheus.
```

Each FAIL or WARN line is printed once, followed by the nodes it applies to and a `->` line with the fix. Fix those, then run `all` again; running it more than once is safe. Full logs are in `logs/<time>-<command>/<node-ip>.log`. To print every line, run with `SHOW=all`.

Then:

1. **Snapshot the four VMs.** If the build fails, you can roll back in minutes.
2. **Build the cluster in Morpheus** as described in `hks-hybrid-kit/README.md`: section 1 (proxy record, appliance URL, separate cloud) and the cluster form. The pod and service CIDRs on the form must match `POD_CIDR` and `SERVICE_CIDR` in `node.env`.
3. After the build, run `bash prep-nodes.sh check`. A WARN about `containerd.service.d/http-proxy.conf` is expected: Morpheus writes that file, and ours (`zz-hks-proxy.conf`) is read after it, so ours wins.

### Other commands

| Command | Use |
| --- | --- |
| `bash prep-nodes.sh check` | Read-only, safe at any time: SSH, sudo, OS, fixed IP, duplicate hostname / machine-id / UUID / MAC, data disk, appliance reachable, proxy and time state. |
| `bash prep-nodes.sh proxy` | Proxy settings only. |
| `bash prep-nodes.sh time [server…]` | Time only. A server given here overrides `NTP_SERVERS`. |
| `bash prep-nodes.sh time-from-master` | For when only the master can reach an NTP server. The master serves time to `NODE_SUBNET` (it installs chrony), then the workers sync from the master. |
| `bash prep-nodes.sh remove-proxy` | Removes every proxy setting the scripts manage. |

To prepare a single node without the runner, copy `prep-proxy.sh`, `prep-time.sh` and `node.env` into one folder on it, then run `sudo bash prep-proxy.sh` and `sudo bash prep-time.sh`. Both scripts take `--check`, which changes nothing.

## 3. What gets written

Before the scripts change or remove any file, they copy it to `/var/backups/hks-node-prep/<time>-proxy|time/`.

**prep-proxy.sh** first **removes** all of the following:

* proxy lines in `/etc/environment`;
* apt proxy files, such as the installer's `90curtin-aptproxy`;
* `/etc/profile.d/*proxy*`;
* root's curl and wget proxy lines;
* the files written by the older `hks-node-proxy.sh`.

It then writes one consistent set:

| Where | Why |
| --- | --- |
| `/etc/environment` | SSH and sudo sessions. |
| `/etc/apt/apt.conf.d/95hks-proxy` | apt goes through the proxy, with pipelining off (the lab proxy breaks with it on), and reaches the appliance directly. |
| `/root/.curlrc`, a block in `/etc/wgetrc` | Install steps run curl or wget as root with an empty environment. |
| `/etc/systemd/system.conf.d/90-hks-proxy.conf` | Default for every service, including ones installed later. |
| `/etc/systemd/system/{containerd,kubelet,morpheus-node,morpheus-node-runsvdir}.service.d/zz-hks-proxy.conf` | Read after Morpheus' own `http-proxy.conf`, so this one wins. |

**NO_PROXY** contains:

* localhost;
* the appliance and `.hpe.lab`;
* every node IP and `NODE_SUBNET`;
* **`POD_CIDR` and `SERVICE_CIDR`**;
* `.svc` and `.cluster.local`;
* `EXTRA_NO_PROXY`.

Without the pod and service ranges, Calico fails with "Gateway Timeout".

After writing, it tests the result:

* root curl with an empty environment reaches download.docker.com, pkgs.k8s.io, registry.k8s.io, registry-1.docker.io, quay.io and github.com (override the list with `TEST_URLS` in `node.env`);
* the appliance answers directly on 443;
* `apt-get update` is clean;
* the running services see the new settings.

**prep-time.sh**

* **Choosing the server.** It uses the servers given on the command line, otherwise `NTP_SERVERS`. If neither is set, it tries the default gateway, the DNS servers and `NTP_CANDIDATES`, keeps the ones that answer, and warns you to pin one in `NTP_SERVERS` so all four nodes use the same server.
* **When no server answers.** It changes nothing and FAILs.
* **What it writes.** `/etc/systemd/timesyncd.conf.d/90-hks.conf`, or for chrony `/etc/chrony/sources.d/hks.sources`, and it comments out the Ubuntu internet pools.
* **Conflicts.** It refuses to touch a node that runs ntpd, ntpsec or openntpd.
* **What it checks.**
  * The node is synchronized.
  * The timezone is right.
  * The clock is within 2 s of **the Morpheus appliance** (from its HTTPS `Date` header).
  * Also, as information only: how far the clock is from internet time, read through the proxy.

## 4. When something FAILs

| Message | Fix |
| --- | --- |
| `cannot SSH to user@ip` | `ssh-copy-id user@ip` from vmemgr. Check that `NODES` and `SSH_USER` are right. |
| `has no passwordless sudo` | Add the sudoers line shown in the summary. |
| `root curl cannot reach: <hosts>` | The proxy blocks those hosts, or `PROXY` is wrong. The build needs every one of them. |
| `vmemgr.hpe.lab does not resolve / answer directly on 443` | Fix DNS, or add it to `/etc/hosts` on every node. Check the route and firewall to the appliance. |
| `no NTP server answers on UDP 123` | NTP does not go through the HTTP proxy. Ask the network team for a reachable NTP server and put it in `NTP_SERVERS`. If only the master can reach one, use `time-from-master`. |
| `clock differs from vmemgr.hpe.lab` | Point the appliance at the same NTP server as the nodes. |
| `same hostname / machine-id / VM UUID` | The VMs are clones. Change the hostname with `sudo hostnamectl set-hostname <name>`. Regenerate the machine-id with `sudo rm /etc/machine-id && sudo systemd-machine-id-setup && sudo reboot`. The UUID has to change in the hypervisor. |
| `/dev/vdb is not empty` | The build needs it empty. `sudo wipefs -a /dev/vdb` **destroys** its data. For nodes from an earlier attempt, use `hks-node-reset.sh` from the hybrid kit. |
| `swap is on` | `sudo swapoff -a` and comment out the swap line in `/etc/fstab`. |

## 5. Ready for Gateway API later

* LoadBalancer and Gateway IPs you take from `192.168.200.0/24` are already in NO_PROXY, because `NODE_SUBNET` covers them. Reserve a small range that DHCP and other VMs don't use, and keep it clear of the proxy at .250.
* The Gateway API CRDs come from GitHub, and the controller images come from public registries. `prep-proxy.sh` already checks that github.com, registry.k8s.io, docker.io and quay.io are reachable through the proxy. If an install later pulls from another host, add it to `TEST_URLS` and run `check`.

Tested in Ubuntu 24.04 containers with an HTTP CONNECT proxy, a fake appliance and a fake NTP server. The tests covered:

* dirty starting state from the installer and the old kit;
* running twice;
* `--check` and `--remove`;
* NTP server discovery, plus dead, unsynchronized and conflicting servers;
* both the timesyncd and chrony paths;
* the runner across 4 nodes plus one unreachable node, including duplicate hostname detection.

systemd itself was stubbed.
