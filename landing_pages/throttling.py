from rest_framework.throttling import AnonRateThrottle


class LandingPageFormSubmissionThrottle(AnonRateThrottle):
    scope = "landing_page_submission"
    rate = "30/minute"

    def get_rate(self):
        try:
            return super().get_rate()
        except Exception:
            return self.rate
