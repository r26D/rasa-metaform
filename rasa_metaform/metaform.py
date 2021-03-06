import pprint
import yaml
from rasa_sdk.forms import FormAction
from rasa_sdk.interfaces import ActionExecutionRejection


def get_all_slots(yml, slots):
    for k, v in yml["slots"].items():
        slots.append(k)
        if "slots" in v:
            get_all_slots(v, slots)
        if "options" in v:
            for o in v["options"]:
                if "slots" in o:
                    get_all_slots(o, slots)
    return slots


def _add_slots(yml, slots, tracker, break_early=False):
    for k, v in yml.items():
        if break_early:
            return None
        slots.append(k)
        if "slots" in v:
            break_early = add_slots(v["slots"], slots, tracker, break_early)
        if "options" in v:
            for o in v["options"]:
                if "action" in o and tracker.get_slot(k) == o.get("value"):
                    return True
                if "slots" in o and tracker.get_slot(k) == o.get("value"):
                    break_early = _add_slots(o["slots"], slots, tracker, break_early)
    return break_early


def _add_responses(yml, responses):
    for slot, values in yml.items():
        response = {}
        if "upload" in values:
            response["custom"] = [{"text": values["utter"], "upload": values["upload"]}]
        else:
            response["text"] = values["utter"]
        if "options" in values:
            response["buttons"] = []
            for button in values["options"]:
                response["buttons"].append(
                    {"title": button["title"], "payload": button["payload"]}
                )
                if "slots" in button:
                    responses = _add_responses(button["slots"], responses)
                if "info" in button:
                    tname = f'utter_info_{slot}_{button["value"]}'
                    responses[tname] = [{"text": button["info"]}]
        responses[f"utter_ask_{slot}"] = [response]
    return responses


class MetaFormAction(FormAction):
    abstract = True
    yml = {"form_name": "_meta_form"}

    def __init_subclass__(cls, files_path=None):
        if files_path:
            cls.files_path = files_path
            with open(f"{files_path}.yml") as f:
                cls.yml = yaml.load(f, Loader=yaml.Loader)
            cls.add_validations(cls.yml["slots"])

    @classmethod
    def name(cls):
        return cls.yml["form_name"]

    @classmethod
    def required_slots(cls, tracker):
        slots = []
        _add_slots(cls.yml["slots"], slots, tracker)
        return slots

    def _add_slots_maps(self, yml, smap):
        for k, v in yml.items():
            if v["type"] == "text" or v["type"] == "doc":
                smap[k] = self.from_text()
            elif v["type"] == "bool":
                smap[k] = [
                    self.from_intent(intent="affirm", value=True),
                    self.from_intent(intent="deny", value=False),
                ]
            elif v["type"] == "entity":
                smap[k] = self.from_entity(entity=k, intent="inform")
            elif v["type"] == "number":
                smap[k] = self.from_entity(entity="number")
            if "slots" in v:
                smap = self._add_slots_maps(v["slots"], smap)
            if "options" in v:
                for o in v["options"]:
                    if "slots" in o:
                        smap = self._add_slots_maps(o["slots"], smap)
        return smap

    def slot_mappings(self):
        smap = {}
        smap = self._add_slots_maps(self.yml["slots"], smap)
        return smap

    def validate(self, dispatcher, tracker, domain):
        "Override default validation so it will ask the question again"
        try:
            return super().validate(dispatcher, tracker, domain)
        except ActionExecutionRejection as e:
            dispatcher.utter_response("utter_default", tracker)
        return []

    @classmethod
    def validate_factory(cls, slot, fnc):
        def validate_slot_response(self, value, dispatcher, tracker, domain):
            return fnc(self, value, dispatcher, tracker, domain)

        setattr(cls, f"validate_{slot}", validate_slot_response)

    @classmethod
    def add_validations(cls, yml):
        for slot, prop in yml.items():
            if "options" in prop:
                for optn in prop["options"]:
                    if "change_slot" in optn:

                        def validate_slot_fn(self, v, d, t, m, s=slot, p=prop):
                            slots = {s: v}
                            for o in p["options"]:
                                if v == o["value"]:
                                    d.utter_response(f"utter_info_{s}_{v}", t)
                                    if "change_slot" in o:
                                        for sn, sv in o["change_slot"].items():
                                            slots[sn] = sv
                            return slots

                        cls.validate_factory(slot, validate_slot_fn)
                    elif "info" in optn:

                        def validate_slot_fn(self, v, d, t, m, s=slot, p=prop):
                            for o in p["options"]:
                                if v == o["value"]:
                                    d.utter_response(f"utter_info_{s}_{v}", t)
                            return {s: v}

                        cls.validate_factory(slot, validate_slot_fn)
                    if "slots" in optn:
                        cls.add_validations(optn["slots"])

    def submit(self, dispatcher, tracker, domain):
        dispatcher.utter_response("utter_submit", tracker)
        context = {}
        for slot in self.required_slots(tracker):
            context[slot] = tracker.get_slot(slot)
        dispatcher.utter_message(pprint.pformat(context))
        return []

    @classmethod
    def domain_responses(cls):
        responses = {}
        responses = _add_responses(cls.yml["slots"], responses)
        return responses

    @classmethod
    def update_domain(cls, domain_file="domain.yml", pre_domain_file="domain-pre.yml"):
        with open(pre_domain_file) as f:
            domain = yaml.load(f, Loader=yaml.Loader)
        if not "forms" in domain:
            domain["forms"] = []
        if not cls.name() in domain["forms"]:
            domain["forms"].append(cls.name())
        responses = cls.domain_responses()
        if not "responses" in domain:
            domain["responses"] = {}
        if not "slots" in domain:
            domain["slots"] = {}
        slots = []
        slots = get_all_slots(cls.yml, slots)
        for slot in slots:
            if not slot in domain["slots"]:
                domain["slots"][slot] = {"type": "unfeaturized"}
        for k, v in responses.items():
            if not k in domain["responses"]:
                domain["responses"][k] = v
        with open(domain_file, "w") as f:
            yaml.dump(domain, f)
