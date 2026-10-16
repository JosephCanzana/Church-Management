"""generate_vapid_keys: print a fresh VAPID key pair for Web Push (run once, paste into .env).

Uses the `cryptography` package already in requirements, so it needs no network. The private key is
the raw 32-byte value and the public key the 65-byte uncompressed point, both base64url without
padding: the formats `pywebpush` and the browser's `applicationServerKey` expect.
"""
import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.core.management.base import BaseCommand


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class Command(BaseCommand):
    help = "Print a new VAPID key pair for Web Push. Paste the two lines into .env."

    def handle(self, *args, **options):
        """Generate one P-256 key pair and print it as .env lines."""
        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_numbers().private_value.to_bytes(32, "big")
        public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        self.stdout.write(f"VAPID_PUBLIC_KEY={_b64url(public)}")
        self.stdout.write(f"VAPID_PRIVATE_KEY={_b64url(private)}")
        self.stdout.write("VAPID_SUBJECT=mailto:you@your-church.org   # use a real address you read")
        self.stderr.write(
            "Keep the private key secret. Changing keys later stops pushes to every device already "
            "turned on: each person must turn notifications off and on again."
        )
