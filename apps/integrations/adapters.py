from django.conf import settings


class BaseIntegrationAdapter:
    provider = "base"

    def submit(self, event):
        raise NotImplementedError


class ETIMSAdapter(BaseIntegrationAdapter):
    provider = "etims"

    def submit(self, event):
        if settings.INTEGRATION_MODE != "sandbox":
            raise RuntimeError("The production eTIMS adapter is not activated.")
        document_number = event.payload.get("document_number") or event.payload.get("internal_receipt_number") or event.id
        return {
            "accepted": True,
            "reference": f"ETIMS-{document_number}",
            "invoice_number": f"SBX-{str(event.id)[:8].upper()}",
            "control_code": f"CTRL-{str(event.id)[-8:].upper()}",
            "signature": f"SIGN-{str(event.id).replace('-', '')[:16].upper()}",
            "qr_payload": f"ETIMS|{event.organization_id}|{document_number}|{event.id}",
            "message": "Sandbox eTIMS submission accepted.",
        }


class MPesaAdapter(BaseIntegrationAdapter):
    provider = "mpesa"

    def submit(self, event):
        if settings.INTEGRATION_MODE != "sandbox":
            raise RuntimeError("The production M-Pesa adapter is not activated.")
        return {"accepted": True, "reference": f"MPESA-{event.id}"}


ADAPTERS = {"etims": ETIMSAdapter(), "mpesa": MPesaAdapter()}
