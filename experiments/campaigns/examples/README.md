# Example campaigns

Campaigns here are written and valid, but not approved to run. The launcher runs only the campaigns in `experiments/campaigns/`.

- [`hv-host-1.json`](hv-host-1.json): the first metal campaign, with the worker host using the whole m8i.metal-48xl. It needs Evan's approval before it runs. It also waits on the vCPU quota (one metal run needs 208 vCPUs with its support host, and the quota is 32), on metal instance types being allowed in the account, and on the harness carrying out Cloud Hypervisor, Firecracker on PCI with a random-number device, and densities above 64.
