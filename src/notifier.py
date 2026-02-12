import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


class EmailNotifier:

    def __init__(self, sender_email: str, app_password: str, recipients: list[str]):
        self.sender_email = sender_email
        self.app_password = app_password
        self.recipients = recipients

    def _send(self, subject: str, body: str):
        msg = MIMEMultipart()
        msg["From"] = self.sender_email
        msg["To"] = ", ".join(self.recipients)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        try:
            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(self.sender_email, self.app_password)
                server.sendmail(self.sender_email, self.recipients, msg.as_string())
            logger.info(f"Email sent to {self.recipients}")
        except Exception as e:
            logger.error(f"Failed to send email: {e}")

    def notify_package_delivered(self, description: str):
        self._send(
            subject="Package Delivered",
            body=f"A package delivery was detected at the front door.\n\n{description}",
        )

    def notify_thief_detected(self, description: str):
        self._send(
            subject="ALERT: Possible Package Thief Detected",
            body=(
                f"Suspicious activity was detected at the front door.\n\n"
                f"{description}\n\n"
                f"Check the Ring app or Google Drive for the flagged image."
            ),
        )
