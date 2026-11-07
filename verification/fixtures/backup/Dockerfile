FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_* && mkdir -p /run/sshd && useradd -m replica && passwd -d replica && install -d -m 0700 -o replica /home/replica/archives
COPY verification/backup_expiry.py verification/backup_replica.py /home/replica/verification/
CMD ["sh","-ec","ssh-keygen -A; exec /usr/sbin/sshd -D -e -o UsePAM=no -o PermitRootLogin=no -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o AuthorizedKeysFile=/etc/ssh/replica.pub -o DisableForwarding=yes"]
