"""Assert committed browser outcomes, inside the verified disposable cluster only."""
import json
import os
from pathlib import Path
from django.db import connection
from foundation.models import SalesQuote, QuotePayment, UserFavorite
with connection.cursor() as cursor:
    cursor.execute("select current_setting('data_directory')")
    assert cursor.fetchone()[0] == os.environ["EXPECTED_DISPOSABLE_DATA"]
fixture = json.loads((Path(os.environ["ACCEPTANCE_ROOT"]) / "browser-private.json").read_text())
quote = SalesQuote.objects.get(pk=fixture["quote_id"])
assert quote.status == "delivered"
payments = QuotePayment.objects.filter(quote=quote)
assert payments.count() == 1
assert str(payments.get().amount) == "4000.00"
assert UserFavorite.objects.filter(user__username="demo_multi", destination="purchasing").exists()
assert UserFavorite.objects.filter(user__username="demo_roche", destination="inventory").exists()
assert not UserFavorite.objects.filter(destination="procurement.progress").exists()
print("Committed real-browser outcomes: delivered quote; exactly one QAR 4000 simulated collection; legacy pins unchanged; function pin removed")
