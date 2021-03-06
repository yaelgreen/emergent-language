import os
import time

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

colors_dict = ['red', 'green', 'blue']
shapes_dict = ['circle', 'triangle']
color_dict = {'red':0,
              'blue':1,
              'green':2}

class Utterance(nn.Module):
    def __init__(self, action_processor_config, utterance_config, dataset_dictionary, use_utterance_old_code):
        super(Utterance, self).__init__()
        self.action_processor_config = action_processor_config
        self.use_utterance_old_code = use_utterance_old_code
        self.utterance_chooser = nn.Sequential(
                    nn.Linear(action_processor_config.action_processor.hidden_size, action_processor_config.hidden_size),
                    nn.ELU(),
                    nn.Linear(action_processor_config.hidden_size, action_processor_config.vocab_size))
        self.gumbel_softmax = GumbelSoftmax(action_processor_config.use_cuda)

        self.dataset_dictionary = dataset_dictionary
        self.word_encoder = nn.Embedding(len(dataset_dictionary.word_dict), utterance_config.nembed_word)
        # a writer, a RNNCell that will be used to generate utterances
        self.writer = nn.GRUCell(
            input_size=utterance_config.nhid_ctx + utterance_config.nembed_word,
            hidden_size=utterance_config.nhid_lang,
            bias=True)
        self.decoder = nn.Linear(utterance_config.nhid_lang, utterance_config.nembed_word)
        self.lm_model = DialogModel(dataset_dictionary.word_dict, None,
                                    None, 4,
                                    utterance_config,
                                    None, self.action_processor_config.mode)
        # if not self.action_processor_config.mode == 'train_utter': #todo reacrivate
        #     with open(utterance_config.folder_dir+'lm_model.pt', 'rb') as f:
        #         self.lm_model.load_state_dict(torch.load(f))
        self.colors_dict_keys = [self.lm_model.word_dict.word2idx[color] for color in colors_dict]
        # self.shapes_dict_keys = [self.lm_model.word_dict.word2idx[shape] for shape in shapes_dict]
        annotation = [[self.lm_model.word_dict.word2idx['red']],
                      [self.lm_model.word_dict.word2idx['blue']],
                      [self.lm_model.word_dict.word2idx['green']]]
        self.crit = Criterion(dataset_dictionary.word_dict, device_id=None, annotation =annotation)
        # self.crit = Criterion(dataset_dictionary.word_dict, device_id=None)

        self.opt = optim.Adam(self.lm_model.parameters(), lr=utterance_config.lr)
        self.config = utterance_config
        # self.loss = torch.zeros(size=(1,))

    def forward(self, processed, full_sentence, step=None, epoch=None):
        self.loss = torch.zeros(size=(1,))
        self.words = torch.LongTensor(size=[self.config.batch_size, DEFAULT_VOCAB_SIZE])
        self.lang_h = self.lm_model.zero_hid(processed.size(0), self.lm_model.config.nhid_lang)
        utter = full_sentence.tolist()
        encoded_utter = [self.dataset_dictionary.word_dict.w2i(utter[i].split(" "))
                         for i in range(len(full_sentence))]
        encoded_pad = self.dataset_dictionary.word_dict.w2i(['<pad>'])
        longest_sentence = DEFAULT_VOCAB_SIZE
        encoded_utter = [encoded_utter[i] + encoded_pad * (longest_sentence - len(encoded_utter[i]))
                         if len(encoded_utter[i]) < longest_sentence else encoded_utter[i]
                         for i in range(len(full_sentence))]
        encoded_utter = Variable(torch.LongTensor(np.array(encoded_utter)))
        encoded_utter_out = encoded_utter
        encoded_utter = encoded_utter.transpose(0, 1)
        encoded_utter = encoded_utter.contiguous()
        inpt = encoded_utter.narrow(0, 0, encoded_utter.size(0) - 1)
        self.tgt = encoded_utter.narrow(0, 1, encoded_utter.size(0) - 1).view(-1)

        if self.action_processor_config.mode == 'train_utter':
            out, lang_h = self.lm_model.forward_lm(inpt, self.lang_h, processed.unsqueeze(0))
            loss = self.crit(out.view(-1, len(self.dataset_dictionary.word_dict)), self.tgt)
            # decode utterance (for plot and for us)
            # try 1 with for loop
            for batch in range(self.config.batch_size):
                self.words[batch, 0] = self.lm_model.word2var('Hi').unsqueeze(1)
                for word_idx in range(0, DEFAULT_VOCAB_SIZE - 1):  # with out the Hi
                    scores = out[word_idx, batch].add(-out[word_idx, batch].max().item())
                    prob = F.softmax(scores, dim=0)
                    word = prob.multinomial(num_samples=1).detach()
                    self.words[batch, word_idx + 1] = word
        else:
            # create initial hidden state for the language rnn and self_words
            self.lang_hs = []
            self.write(self.lang_h, processed.unsqueeze(0)) #undecoded utter, to decode it use: self._decode(utter, self.lm_model.word_dict)
        utter_print = '' ##remove
        utter_print = self.lm_model.word_dict.i2w(self.words[1].data.cpu()) # [str(self.total_loss)]
        utter_print = ' '.join(utter_print)
        if self.action_processor_config.mode == 'train_utter':
            with open(self.config.folder_dir+os.sep+"utterance_out_fb.csv", 'a', newline='') as f:
                f.write(utter_print)
                # f.write('\n')
            if epoch == 100:
                for param_group in self.opt.param_groups:
                    param_group['lr'] = 0.000001
                    print(param_group['lr'])
            self.opt.zero_grad()
            # backward step with gradient clipping, use retain_graph=True
            loss.backward(retain_graph=True)
            torch.nn.utils.clip_grad_norm_(self.lm_model.parameters(),
                                           self.config.clip)
            self.opt.step()
            with open(self.config.folder_dir+os.sep+'lm_model.pt', 'wb') as f:
                torch.save(self.lm_model.state_dict(), f)
            print(loss, epoch)
            return loss, self.words , self.config.folder_dir
        else:
            with open(self.config.folder_dir+os.sep+"utterance_out_fine_tune.csv", 'a', newline='') as f:
                f.write(utter_print)
                f.write('\n')
            self.opt.zero_grad()
            # print(self.total_loss)
            # print(self.dataset_dictionary.word_dict.i2w(word[1, :]))
            return self.loss, self.words, encoded_utter_out

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

    def write(self, lang_h, processed):
        # generate a new utterance #todo Start HERE!
        self.lang_h = lang_h
        outs, self.lang_h, lang_hs, self.loss = self.lm_model.write(self.lang_h, processed, DEFAULT_VOCAB_SIZE-1 ,
                                                               self.config.temperature, self.loss, self.tgt)
        # if self.step == 0:
        #     self.total_loss = self.crit(scores.view(-1, len(self.dataset_dictionary.word_dict.idx2word)), self.tgt)
        #     # print(id(scores))
        # else:
        #     self.total_loss += self.crit(scores.view(-1, len(self.dataset_dictionary.word_dict.idx2word)), self.tgt)
            # print(id(scores))
        outs = torch.transpose(outs,0,1)
        self.lang_hs.append(lang_hs)
        # first add the special 'Hi' token
        self.words[:,0] = self.lm_model.word2var('Hi').unsqueeze(1)  # change to Hi
        # then append the utterance
        self.words[:,1:] = outs
        # assert (torch.cat(self.words).size()[0] == torch.cat(self.lang_hs).size()[0]) #todo debag
        # if self.total_loss == 0:
        #     self.total_loss = loss
        # else:
        #     self.total_loss = self.total_loss.clone() + loss
        # # decode into English words use function
        #self._decode = dictionary.i2w(out.data.cpu())
        # return self._decode(outs, self.lm_model.word_dict)
