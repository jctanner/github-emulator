# -*- mode: ruby -*-
# vi: set ft=ruby :

Vagrant.configure("2") do |config|

  # --- Server VM: runs the GitHub emulator in Docker ---
  config.vm.define "server", primary: true do |server|
    server.vm.box = "debian/bookworm64"
    server.vm.hostname = "ghemu"

    server.vm.network "private_network",
      ip: "192.168.123.10",
      libvirt__network_name: "ghemu_net",
      libvirt__dhcp_enabled: false,
      libvirt__forward_mode: "none"

    server.vm.synced_folder ".", "/vagrant", disabled: true

    server.vm.provider :libvirt do |lv|
      lv.uri = "qemu:///system"
      lv.cpus = 2
      lv.memory = 2048
    end

    server.vm.provision "shell", inline: <<-SHELL
      set -eux
      export DEBIAN_FRONTEND=noninteractive

      apt-get update
      apt-get install -y ca-certificates curl gnupg rsync

      # Docker CE from official repo
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      chmod a+r /etc/apt/keyrings/docker.gpg

      echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/debian \
        $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list

      apt-get update
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

      usermod -aG docker vagrant
      mkdir -p /srv/github_emulator
      chown vagrant:vagrant /srv/github_emulator

      echo "Docker provisioning complete."
      docker --version
      docker compose version
    SHELL
  end

  # --- Client VM: clean environment for testing with the gh CLI ---
  config.vm.define "client" do |client|
    client.vm.box = "debian/bookworm64"
    client.vm.hostname = "ghemu-client"

    client.vm.network "private_network",
      ip: "192.168.123.11",
      libvirt__network_name: "ghemu_net",
      libvirt__dhcp_enabled: false,
      libvirt__forward_mode: "none"

    client.vm.synced_folder ".", "/vagrant", disabled: true

    client.vm.provider :libvirt do |lv|
      lv.uri = "qemu:///system"
      lv.cpus = 1
      lv.memory = 512
    end

    client.vm.provision "shell", inline: <<-SHELL
      set -eux
      export DEBIAN_FRONTEND=noninteractive

      apt-get update
      apt-get install -y ca-certificates curl git jq rsync

      # Point ghemu.local at the server VM
      echo "192.168.123.10 ghemu.local" >> /etc/hosts

      mkdir -p /srv/bin /srv/scripts
      chown -R vagrant:vagrant /srv

      echo "Client provisioning complete."
    SHELL
  end

end
