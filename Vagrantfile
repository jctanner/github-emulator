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

  # --- Runner VM: GitHub Actions runner that registers with the emulator ---
  config.vm.define "runner" do |runner|
    runner.vm.box = "generic/ubuntu2204"
    runner.vm.hostname = "actions-runner"

    runner.vm.network "private_network",
      ip: "192.168.123.12",
      libvirt__network_name: "ghemu_net",
      libvirt__dhcp_enabled: false,
      libvirt__forward_mode: "none"

    runner.vm.synced_folder ".", "/vagrant", disabled: true

    runner.vm.provider :libvirt do |lv|
      lv.uri = "qemu:///system"
      lv.cpus = 2
      lv.memory = 2048
    end

    runner.vm.provision "file", source: "src/runners/emulator/runner.py", destination: "/tmp/runner.py"

    runner.vm.provision "shell", env: {
      "RUNNER_TYPE" => ENV.fetch("RUNNER_TYPE", "custom"),
      "GITHUB_EMULATOR_URL" => ENV.fetch("GITHUB_EMULATOR_URL", "https://192.168.123.10"),
      "GITHUB_EMULATOR_TOKEN" => ENV.fetch("GITHUB_EMULATOR_TOKEN", ""),
      "RUNNER_REPO" => ENV.fetch("RUNNER_REPO", "admin/test-repo"),
      "RUNNER_NAME" => ENV.fetch("RUNNER_NAME", "vagrant-runner-1"),
      "RUNNER_LABELS" => ENV.fetch("RUNNER_LABELS", "self-hosted,linux,x64"),
    }, inline: <<-SHELL
      set -eux
      export DEBIAN_FRONTEND=noninteractive

      apt-get update
      apt-get install -y curl jq git python3 python3-pip openssl

      # Point ghemu.local at the server VM
      echo "192.168.123.10 ghemu.local" >> /etc/hosts

      # Trust the emulator's self-signed cert
      echo | openssl s_client -connect 192.168.123.10:443 2>/dev/null \
        | openssl x509 > /usr/local/share/ca-certificates/ghemu.crt || true
      update-ca-certificates || true

      if [ "$RUNNER_TYPE" = "real" ]; then
        # Download and configure the real GitHub Actions runner
        mkdir -p /opt/actions-runner && cd /opt/actions-runner
        RUNNER_VERSION=$(curl -s https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name | tr -d v)
        curl -sL "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" | tar xz
        chown -R vagrant:vagrant /opt/actions-runner

        # Get registration token from emulator
        REG_TOKEN=$(curl -sk -X POST \
          -H "Authorization: token ${GITHUB_EMULATOR_TOKEN}" \
          "${GITHUB_EMULATOR_URL}/api/v3/repos/${RUNNER_REPO}/actions/runners/registration-token" \
          | jq -r .token)

        sudo -u vagrant ./config.sh \
          --url "${GITHUB_EMULATOR_URL}/${RUNNER_REPO}" \
          --token "${REG_TOKEN}" \
          --name "${RUNNER_NAME}" \
          --labels "${RUNNER_LABELS}" \
          --unattended \
          --replace || echo "Real runner config.sh failed (expected until Phase 3 GHES endpoints are done)"

        ./svc.sh install vagrant || true
        ./svc.sh start || true
      else
        # Custom Python runner
        pip3 install httpx --break-system-packages
        cp /tmp/runner.py /opt/runner.py

        cat > /etc/systemd/system/actions-runner.service <<UNIT
[Unit]
Description=GitHub Emulator Actions Runner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=vagrant
Environment=GITHUB_EMULATOR_URL=${GITHUB_EMULATOR_URL}
Environment=GITHUB_EMULATOR_TOKEN=${GITHUB_EMULATOR_TOKEN}
Environment=RUNNER_REPO=${RUNNER_REPO}
Environment=RUNNER_NAME=${RUNNER_NAME}
Environment=RUNNER_LABELS=${RUNNER_LABELS}
ExecStart=/usr/bin/python3 /opt/runner.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

        systemctl daemon-reload
        systemctl enable --now actions-runner
      fi

      echo "Runner provisioning complete (type: $RUNNER_TYPE)."
    SHELL

  end

end
