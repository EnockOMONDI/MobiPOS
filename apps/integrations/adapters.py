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
        return {"accepted": True, "reference": f"ETIMS-{event.id}"}


class MPesaAdapter(BaseIntegrationAdapter):
    provider = "mpesa"

    def submit(self, event):
        if settings.INTEGRATION_MODE != "sandbox":
            raise RuntimeError("The production M-Pesa adapter is not activated.")
        return {"accepted": True, "reference": f"MPESA-{event.id}"}


ADAPTERS = {"etims": ETIMSAdapter(), "mpesa": MPesaAdapter()}
