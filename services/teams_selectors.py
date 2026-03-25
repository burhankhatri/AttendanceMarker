TEAMS_SELECTOR_MANIFEST = {
    'app_prompt': {
        'xpath': [
            '//button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "continue on this browser")]',
            '//button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "use the web app instead")]',
            '//a[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "continue on this browser")]',
        ],
        'css': [
            'button[data-tid="joinOnWeb"]',
            'button[data-tid="continueOnBrowser"]',
            'button[data-tid="continue-in-browser"]',
        ],
    },
    'prejoin': {
        'mic': {
            'css': [
                '[data-tid="toggle-mute"]',
                '[data-tid="prejoin-toggle-mute"]',
                '[aria-label*="microphone" i][role*="button" i]',
                '[title*="microphone" i][role*="button" i]',
                '[aria-label*="mic" i]',
                'button[aria-label*="microphone" i]',
            ],
        },
        'camera': {
            'css': [
                '[data-tid="toggle-video"]',
                '[data-tid="prejoin-toggle-video"]',
                '[aria-label*="camera" i][role*="button" i]',
                '[title*="camera" i][role*="button" i]',
                'button[aria-label*="camera" i]',
            ],
        },
    },
    'join': {
        'xpath': [
            '//button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "join now")]',
            '//button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "join")]',
            '//span[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "join now")]/ancestor::button',
        ],
        'css': [
            'button[data-tid="prejoin-join-button"]',
            'button[data-tid="join-button"]',
            'button[aria-label*="join now" i]',
        ],
    },
    'in_meeting': {
        'xpath': [
            '//*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "you are in the lobby")]',
            '//*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "someone in the meeting should let you in")]',
        ],
    },
    'captions': {
        'css': [
            '[data-tid="closed-caption-text"]',
            '[data-tid="closed-caption-text-with-label"]',
            '[data-tid*="caption"] span',
            '[data-tid="captions-renderer"] span',
            '.captions-container span',
        ],
    },
}

TEAMS_AUTH_HOST_HINTS = (
    'login.microsoftonline.com',
    'login.live.com',
    'account.microsoft.com',
    'microsoftonline.com',
)

TEAMS_LOBBY_TEXT_HINTS = (
    'you are in the lobby',
    'someone in the meeting should let you in',
    'we will let people in soon',
    'waiting in the lobby',
)
