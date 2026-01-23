INSTALL_DIR ?= /opt/mist-userid
CONF_DIR ?= /etc/mist-userid
VENV := $(INSTALL_DIR)/venv
PYTHON := $(VENV)/bin/python
SERVICE_DIR := /etc/systemd/system

.PHONY: install update configure selinux firewall deploy test status start stop restart logs clean

install:
	mkdir -p $(INSTALL_DIR)
	cp -r app requirements.txt $(INSTALL_DIR)/
	python3 -m venv $(VENV)
	$(VENV)/bin/python -m ensurepip
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r $(INSTALL_DIR)/requirements.txt
	$(VENV)/bin/pip uninstall pip setuptools -y
	rm -rf $(VENV)/bin/pip*

update:
	$(VENV)/bin/python -m ensurepip
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r $(INSTALL_DIR)/requirements.txt --upgrade
	$(VENV)/bin/pip uninstall pip setuptools -y
	rm -rf $(VENV)/bin/pip*

configure:
	mkdir -p $(CONF_DIR)
	@if [ ! -f $(CONF_DIR)/env ]; then \
		cp deploy/env.example $(CONF_DIR)/env; \
		chmod 600 $(CONF_DIR)/env; \
		echo "Created $(CONF_DIR)/env — edit it with your settings"; \
	else \
		echo "$(CONF_DIR)/env already exists, skipping"; \
	fi

selinux:
	semanage port -l | grep -q "http_port_t.*8000" || \
		semanage port -a -t http_port_t -p tcp 8000
	semanage fcontext -a -t bin_t "$(INSTALL_DIR)/venv/bin(/.*)?" 2>/dev/null || \
		semanage fcontext -m -t bin_t "$(INSTALL_DIR)/venv/bin(/.*)?"
	restorecon -Rv $(INSTALL_DIR)/venv/bin
	restorecon -Rv $(CONF_DIR) 2>/dev/null || true
	setsebool -P httpd_can_network_connect 1
	@echo "SELinux contexts and booleans configured"

firewall:
	firewall-cmd --permanent --add-port=8000/tcp
	firewall-cmd --reload
	@echo "Firewall: port 8000/tcp opened"

deploy: install configure selinux firewall
	cp deploy/mist-userid-api.service $(SERVICE_DIR)/
	cp deploy/mist-userid-worker.service $(SERVICE_DIR)/
	systemctl daemon-reload
	systemctl enable mist-userid-api mist-userid-worker
	systemctl start mist-userid-api mist-userid-worker
	@echo "Services deployed and started"

test:
	$(VENV)/bin/python -m ensurepip
	$(VENV)/bin/pip install -r $(INSTALL_DIR)/requirements-dev.txt
	$(VENV)/bin/python -m pytest -v
	$(VENV)/bin/pip uninstall pip setuptools -y
	rm -rf $(VENV)/bin/pip*

status:
	@systemctl status mist-userid-api --no-pager || true
	@echo "---"
	@systemctl status mist-userid-worker --no-pager || true
	@echo "---"
	@curl -s http://localhost:8000/health 2>/dev/null || echo "API not responding"
	@curl -s http://localhost:8000/ready 2>/dev/null || echo "Readiness check failed"

start:
	systemctl start mist-userid-api mist-userid-worker

stop:
	systemctl stop mist-userid-api mist-userid-worker

restart:
	systemctl restart mist-userid-api mist-userid-worker

logs:
	journalctl -u mist-userid-api -u mist-userid-worker -f

clean:
	systemctl stop mist-userid-api mist-userid-worker || true
	systemctl disable mist-userid-api mist-userid-worker || true
	rm -f $(SERVICE_DIR)/mist-userid-api.service $(SERVICE_DIR)/mist-userid-worker.service
	systemctl daemon-reload
	firewall-cmd --permanent --remove-port=8000/tcp 2>/dev/null || true
	firewall-cmd --reload 2>/dev/null || true
	rm -rf $(INSTALL_DIR)
