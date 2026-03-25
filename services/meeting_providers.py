class BaseJoinFlow:
    provider = None

    def join(self, bot):
        raise NotImplementedError

    def collect_caption_lines(self, bot):
        return []


class MeetJoinFlow(BaseJoinFlow):
    provider = 'meet'

    def join(self, bot):
        return bot._join_meet_flow()


class TeamsJoinFlow(BaseJoinFlow):
    provider = 'teams'

    def join(self, bot):
        return bot._join_teams_flow()

    def collect_caption_lines(self, bot):
        return bot._collect_teams_caption_lines()


def build_join_flow(provider):
    if provider == 'teams':
        return TeamsJoinFlow()
    return MeetJoinFlow()
