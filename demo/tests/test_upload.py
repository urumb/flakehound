import time

import shop


def test_upload_finishes():
    up = shop.Uploader()
    up.start()
    time.sleep(0.15)  # "should be long enough" (it isn't on a busy machine)
    assert up.done
