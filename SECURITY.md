# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| 0.1.x   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public GitHub issue for security vulnerabilities
2. Use [GitHub's private vulnerability reporting](https://github.com/WinSe7en/mist-userid/security/advisories/new) to submit your report
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We will acknowledge receipt within 48 hours and provide a detailed response within 7 days.

## Security Considerations

### Webhook Authentication

- All incoming webhooks are validated using HMAC-SHA256 signatures
- Invalid signatures are rejected with 401 Unauthorized
- The `MIST_WEBHOOK_SECRET` should be a strong, randomly generated string

### PA Firewall Credentials

- API keys and passwords are read from environment variables, not code
- Use `PA_USERNAME`/`PA_PASSWORD` for auto-generated keys (preferred over static keys)
- Ensure `/etc/mist-userid/env` has restricted permissions (`chmod 600`)

### Network Security

- Use HTTPS for PA firewall connections (certificate validation enabled by default)
- Consider running behind a reverse proxy with TLS termination
- The webhook endpoint should only be accessible from Mist cloud IPs

### Systemd Hardening

The included systemd units apply security hardening:

- `NoNewPrivileges=yes`
- `ProtectSystem=strict`
- `PrivateTmp=yes`
- Memory limits to prevent resource exhaustion

## Best Practices

1. Run with least-privilege PA service account
2. Use dedicated Redis instance (not shared with other services)
3. Monitor the dead-letter queue for failed operations
4. Rotate PA credentials periodically
5. Keep the software updated
