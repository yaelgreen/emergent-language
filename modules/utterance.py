import torch
import torch.nn as nn
from torch.autograd import Variable
from torch import optim
import numpy as np
from modules.gumbel_softmax import GumbelSoftmax
from configs import DEFAULT_VOCAB_SIZE
import torch.nn.functional as F
from modules.dialog_model import DialogModel
from modules.modules_for_lm import Criterion


class Utterance(nn.Module):
    def __init__(self, config, dataset_dictionary, use_utterance_old_code):
        super(Utterance, self).__init__()
        self.use_utterance_old_code = use_utterance_old_code

        self.utterance_chooser = nn.Sequential(
                    nn.Linear(config.action_processor.hidden_size, config.hidden_size),
                    nn.ELU(),
                    nn.Linear(config.hidden_size, config.vocab_size))
        self.gumbel_softmax = GumbelSoftmax(config.use_cuda)

        self.args = {'init_range': 0.1, 'nhid_lang': 256, 'nembed_word': 10,
                 'nhid_ctx': 256, 'dropout': 0.5, 'momentum':0.1,
                 'lr':1, 'nesterov':True, 'clip':0.5, 'batch_size':512, 'temperature':0.5}
        self.dataset_dictionary = dataset_dictionary
        self.lm_model = DialogModel(dataset_dictionary.word_dict, None,
                                None, 4,
                                self.args,
                                None)
        self.crit = Criterion(dataset_dictionary.word_dict, device_id=None)
        self.opt = optim.SGD(self.lm_model.parameters(), lr=self.args['lr'],
                         momentum=self.args['momentum'],
                         nesterov=(self.args['nesterov'] and self.args['momentum'] > 0))
        self.total_loss = 0
        # embedding for words
        self.word_encoder = nn.Embedding(len(dataset_dictionary.word_dict), self.args['nembed_word'])
        # a writer, a RNNCell that will be used to generate utterances
        self.writer = nn.GRUCell(
            input_size=self.args['nhid_ctx'] + self.args['nembed_word'],
            hidden_size=self.args['nhid_lang'],
            bias=True)
        self.decoder = nn.Linear(self.args['nhid_lang'], self.args['nembed_word'])


    def forward(self, processed, full_sentence,mode=None):

        # perform forward for the language model
        utter = full_sentence.tolist()
        encoded_utter = np.array([self.dataset_dictionary.word_dict.w2i(utter[i].split(" "))
                                  for i in range(len(full_sentence))])
        encoded_pad = self.dataset_dictionary.word_dict.w2i(['<pad>'])
        longst_sentence = len(max(encoded_utter,key=len))
        encoded_utter = [encoded_utter[i] + encoded_pad * (longst_sentence - len(encoded_utter[i]))
                         if len(encoded_utter[i]) < longst_sentence else encoded_utter[i]
                         for i in range(len(full_sentence))]
        encoded_utter = Variable(torch.LongTensor(encoded_utter))
        encoded_utter = encoded_utter.transpose(0,1)

        # create initial hidden state for the language rnn
        lang_h = self.lm_model.zero_hid(processed.size(0), self.lm_model.args['nhid_lang'])
        out, lang_h = self.lm_model.forward_lm(encoded_utter, lang_h, processed.unsqueeze(0))

        # remove batch dimension from the language and context hidden states
        lang_h = lang_h.squeeze(1)

        # if we start a new sentence, prepend it with 'Hi'
        inpt2 = Variable(torch.LongTensor(1, self.args['batch_size']))
        inpt2.data.fill_(self.dataset_dictionary.word_dict.get_idx('Hi'))

        # max_words = 20
        # temperature = 0.5
        # for _ in range(max_words):
        #     if inpt2 is not None:
        #         # add the context to the word embedding
        #         inpt_emb = torch.cat([self.word_encoder(inpt2), processed.unsqueeze(0)], 1).squeeze(0)
        #         # update RNN state with last word
        #         lang_h = self.writer(inpt_emb, lang_h)
        #         lang_hs.append(lang_h)

        # decode words using the inverse of the word embedding matrix
        out2 = self.decoder(lang_h)
        scores = F.linear(out2, self.word_encoder.weight).div(self.args['temperature'])
        # subtract constant to avoid overflows in exponentiation
        scores = scores.add(-scores.max().item()).squeeze(0)

        # # disable special tokens from being generated in a normal turns
        # if not resume:
        #     mask = Variable(self.special_token_mask)
        #     scores = scores.add(mask)

        prob = F.softmax(scores, dim=2)  # TODO: is this th right dim?
        logprob = F.log_softmax(scores, dim=2)  # TODO: is this th right dim?

        # explicitly defining num_samples for pytorch 0.4.1
        # word = prob.multinomial(num_samples=512).detach() #TODO: is this th right num_samples?
        word = torch.transpose(prob, 0, 1).multinomial(num_samples=DEFAULT_VOCAB_SIZE).detach()
        # print(self.dataset_dictionary.word_dict.i2w(word[1,:]))
        # logprob = logprob.gather(0, word)

        # logprobs.append(logprob)
        # outs.append(word.view(word.size()[0], 1))

        tgt = encoded_utter.reshape(encoded_utter.shape[0]*encoded_utter.shape[1])

        loss = self.crit(out.view(-1, len(self.dataset_dictionary.word_dict)), tgt) # in FB code the inpt and tgt is one demintion less than the original data
        if mode is None:
            self.opt.zero_grad()
            # backward step with gradient clipping
            # loss.backward()
            loss.backward(retain_graph=True)
            torch.nn.utils.clip_grad_norm_(self.lm_model.parameters(),
                                           self.args['clip'])
            self.opt.step()
            return loss, word
        else:
            self.total_loss += loss
            return self.total_loss, word


    def create_utterance_using_old_code(self, training, processed):
        utter = self.utterance_chooser(processed)
        if training:
            utterance = self.gumbel_softmax(utter)
        else:
            utterance = torch.zeros(utter.size())
            if self.using_cuda:
                utterance = utterance.cuda()
            max_utter = utter.max(1)[1]
            max_utter = max_utter.data[0]
            utterance[0, max_utter] = 1
        return utterance